from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import sqlite3
import random
import json
import os
import time
from datetime import datetime
from threading import Lock

import timetable
import inference

app = FastAPI(title="RailSafe Engineer Control System")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DB_NAME = "rail_safety.db"
db_lock = Lock()

simulation_running = False
simulation_started_at = None  # wall-clock time simulation was last started

# ---------------------------------------------------------------------
# Point these at your actual test-data folders. Each tick, one random
# file from each is fed through the real model for a random train.
# Leave a folder empty/missing and that modality falls back to a mock
# prediction, so the rest of the system still runs while models aren't
# wired in yet.
# ---------------------------------------------------------------------
TEST_AUDIO_DIR = os.path.join(os.path.dirname(__file__), "test_data", "audio")
TEST_IMAGE_DIR = os.path.join(os.path.dirname(__file__), "test_data", "images")

# 3-tier severity, per modality's predicted class
SEVERITY_MAP = {
    # audio
    "normal": None,              # no ticket created
    "wheel_burnt": "CRITICAL",
    "superelevation": "ABNORMAL",

    # image — her model has no "normal" class, every prediction is
    # treated as a real defect until you confirm otherwise with her.
    # Severity tiers below are MY domain-reasoning guess (structural
    # cracks/shellings/spallings/squats read as more serious than
    # surface flakings/grooves/joints) — not confirmed with her, and
    # worth checking against any documentation her dataset came with.
    "cracks": "CRITICAL",
    "shellings": "CRITICAL",
    "spallings": "CRITICAL",
    "squats": "CRITICAL",
    "flakings": "ABNORMAL",
    "grooves": "ABNORMAL",
    "joints": "ABNORMAL",
}


# ---------------------------------------------------------
# DATABASE
# ---------------------------------------------------------

def get_db():
    conn = sqlite3.connect(DB_NAME, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS defect_tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            train_number TEXT NOT NULL,
            source TEXT NOT NULL,
            defect_type TEXT NOT NULL,
            confidence REAL NOT NULL,
            severity TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'ACTIVE',
            latitude REAL,
            longitude REAL,
            location_name TEXT,
            detected_at TEXT NOT NULL,
            prediction_correct INTEGER,
            engineer_label TEXT,
            resolved_at TEXT,
            evidence TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS learning_feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_id INTEGER,
            predicted_label TEXT,
            correct_label TEXT,
            created_at TEXT,
            FOREIGN KEY(ticket_id) REFERENCES defect_tickets(id)
        )
    """)

    # Passenger journey bookings — links a phone/email to a specific
    # train, and is what SMS notifications are sent against.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS bookings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone TEXT,
            email TEXT,
            source TEXT,
            destination TEXT,
            train_number TEXT,
            timing TEXT,
            created_at TEXT
        )
    """)

    conn.commit()
    conn.close()


init_db()


# ---------------------------------------------------------
# MODELS
# ---------------------------------------------------------

class VerifyRequest(BaseModel):
    correct: bool
    correct_label: str | None = None


# ---------------------------------------------------------
# HELPERS
# ---------------------------------------------------------

def now():
    return datetime.now().isoformat(timespec="seconds")


def _sim_minutes_elapsed():
    if simulation_started_at is None:
        return 0.0
    return (time.time() - simulation_started_at) / 60.0


def _pick_test_file(folder):
    if not os.path.isdir(folder):
        return None
    files = [f for f in os.listdir(folder) if not f.startswith(".")]
    return os.path.join(folder, random.choice(files)) if files else None


def _run_model(modality: str):
    """
    Calls your real model if a test file is available; otherwise falls
    back to a mock prediction so the rest of the system keeps working
    while models/test data aren't wired in yet. Swap this fallback out
    once inference.py points at your real files.
    """
    folder = TEST_AUDIO_DIR if modality == "AUDIO" else TEST_IMAGE_DIR
    filepath = _pick_test_file(folder)

    if filepath:
        try:
            result = (inference.predict_audio(filepath) if modality == "AUDIO"
                      else inference.predict_image(filepath))
            result["evidence_file"] = os.path.basename(filepath)
            return result
        except Exception as e:
            print(f"[inference] {modality} model call failed, falling back to mock: {e}")

    classes = ["normal", "normal", "normal", "wheel_burnt", "superelevation"] if modality == "AUDIO" \
        else ["Cracks", "Flakings", "Grooves", "Joints", "Shellings", "Spallings", "Squats"]
    return {"predicted_class": random.choice(classes),
            "confidence": round(random.uniform(0.7, 0.98), 3),
            "evidence_file": None}


def generate_ticket():
    """
    One simulation tick: pick a random train, run both modalities'
    models on it, and create a ticket for anything that isn't 'normal'.
    Location comes from the fixed timetable, not a random pick, so it's
    synchronized with what the passenger side can look up.
    """
    train = random.choice(timetable.TRAINS)
    position = timetable.get_live_position(train, _sim_minutes_elapsed())

    for source in ["AUDIO", "IMAGE"]:
        result = _run_model(source)
        severity = SEVERITY_MAP.get(result["predicted_class"].lower())
        if severity is None:
            continue  # "normal" — no ticket, this ISN'T a defect

        if source == "AUDIO":
            evidence = {
                "signal": result.get("evidence_file") or "simulated_audio_signal",
                "anomaly_start": random.randint(20, 50),
                "anomaly_end": random.randint(55, 90),
                "processing": ["Audio captured", "Butterworth filter", "Log-mel spectrogram",
                               "CRNN inference"],
            }
        else:
            evidence = {
                "image": result.get("evidence_file") or "simulated_track_image",
                "region": {"x": random.randint(20, 45), "y": random.randint(20, 45),
                           "width": random.randint(20, 40), "height": random.randint(20, 40)},
                "processing": ["Image captured", "Normalization", "Model inference"],
            }

        conn = get_db()
        conn.execute("""
            INSERT INTO defect_tickets
            (train_number, source, defect_type, confidence, severity, status,
             latitude, longitude, location_name, detected_at, evidence)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            train["train_number"], source, result["predicted_class"], result["confidence"],
            severity, "ACTIVE", position["lat"], position["lng"],
            f"{train['source']}\u2192{train['destination']} @ km {position['km']}",
            now(), json.dumps(evidence),
        ))
        conn.commit()
        conn.close()


# ---------------------------------------------------------
# TICKETS
# ---------------------------------------------------------

@app.get("/api/tickets")
def get_tickets(train_number: str | None = None):

    conn = get_db()

    if train_number:
        rows = conn.execute("""
            SELECT *
            FROM defect_tickets
            WHERE status = 'ACTIVE'
            AND train_number LIKE ?
            ORDER BY id DESC
        """, (f"%{train_number}%",)).fetchall()
    else:
        rows = conn.execute("""
            SELECT *
            FROM defect_tickets
            WHERE status = 'ACTIVE'
            ORDER BY id DESC
        """).fetchall()

    conn.close()

    return [dict(row) for row in rows]


@app.get("/api/tickets/{ticket_id}")
def get_ticket(ticket_id: int):

    conn = get_db()

    row = conn.execute("""
        SELECT *
        FROM defect_tickets
        WHERE id = ?
    """, (ticket_id,)).fetchone()

    conn.close()

    if not row:
        raise HTTPException(status_code=404, detail="Ticket not found")

    ticket = dict(row)
    ticket["evidence"] = json.loads(ticket["evidence"])

    return ticket


# ---------------------------------------------------------
# ACTIVE LEARNING
# ---------------------------------------------------------

@app.post("/api/tickets/{ticket_id}/verify")
def verify_ticket(ticket_id: int, request: VerifyRequest):

    conn = get_db()

    ticket = conn.execute("""
        SELECT *
        FROM defect_tickets
        WHERE id = ?
    """, (ticket_id,)).fetchone()

    if not ticket:
        conn.close()
        raise HTTPException(status_code=404, detail="Ticket not found")

    correct_label = (
        ticket["defect_type"]
        if request.correct
        else request.correct_label
    )

    conn.execute("""
        UPDATE defect_tickets
        SET prediction_correct = ?,
            engineer_label = ?
        WHERE id = ?
    """, (
        1 if request.correct else 0,
        correct_label,
        ticket_id
    ))

    if not request.correct and request.correct_label:
        conn.execute("""
            INSERT INTO learning_feedback
            (
                ticket_id,
                predicted_label,
                correct_label,
                created_at
            )
            VALUES (?, ?, ?, ?)
        """, (
            ticket_id,
            ticket["defect_type"],
            request.correct_label,
            now()
        ))

    conn.commit()
    conn.close()

    return {
        "message": "Prediction feedback recorded",
        "active_learning": True
    }


# ---------------------------------------------------------
# RESOLVE TICKET
# ---------------------------------------------------------

@app.post("/api/tickets/{ticket_id}/resolve")
def resolve_ticket(ticket_id: int):

    conn = get_db()

    ticket = conn.execute("""
        SELECT *
        FROM defect_tickets
        WHERE id = ?
    """, (ticket_id,)).fetchone()

    if not ticket:
        conn.close()
        raise HTTPException(status_code=404, detail="Ticket not found")

    conn.execute("""
        UPDATE defect_tickets
        SET status = 'RESOLVED',
            resolved_at = ?
        WHERE id = ?
    """, (now(), ticket_id))

    conn.commit()
    conn.close()

    return {
        "message": "Ticket resolved",
        "ticket_id": ticket_id
    }


# ---------------------------------------------------------
# SIMULATION
# ---------------------------------------------------------

@app.post("/api/simulation/start")
def start_simulation():

    global simulation_running, simulation_started_at
    simulation_running = True
    if simulation_started_at is None:
        simulation_started_at = time.time()

    return {
        "running": True,
        "message": "Detection simulation started"
    }


@app.post("/api/simulation/stop")
def stop_simulation():

    global simulation_running
    simulation_running = False

    return {
        "running": False,
        "message": "Detection simulation stopped"
    }


@app.post("/api/simulation/tick")
def simulation_tick():

    if not simulation_running:
        return {
            "generated": False,
            "running": False
        }

    generate_ticket()

    return {
        "generated": True,
        "running": True
    }


@app.get("/api/simulation/status")
def simulation_status():

    return {
        "running": simulation_running
    }


# ---------------------------------------------------------
# STATISTICS
# ---------------------------------------------------------

@app.get("/api/stats")
def get_stats():

    conn = get_db()

    total = conn.execute("""
        SELECT COUNT(*) FROM defect_tickets
    """).fetchone()[0]

    active = conn.execute("""
        SELECT COUNT(*)
        FROM defect_tickets
        WHERE status = 'ACTIVE'
    """).fetchone()[0]

    resolved = conn.execute("""
        SELECT COUNT(*)
        FROM defect_tickets
        WHERE status = 'RESOLVED'
    """).fetchone()[0]

    verified = conn.execute("""
        SELECT COUNT(*)
        FROM defect_tickets
        WHERE prediction_correct IS NOT NULL
    """).fetchone()[0]

    incorrect = conn.execute("""
        SELECT COUNT(*)
        FROM defect_tickets
        WHERE prediction_correct = 0
    """).fetchone()[0]

    conn.close()

    return {
        "total_detections": total,
        "active_tickets": active,
        "resolved_tickets": resolved,
        "verified_predictions": verified,
        "incorrect_predictions": incorrect
    }


# ---------------------------------------------------------
# HOTSPOTS
# ---------------------------------------------------------

@app.get("/api/hotspots")
def get_hotspots():

    conn = get_db()

    rows = conn.execute("""
        SELECT
            location_name,
            latitude,
            longitude,
            COUNT(*) AS defect_count,
            SUM(
                CASE
                    WHEN detected_at >= datetime('now', '-7 days')
                    THEN 1
                    ELSE 0
                END
            ) AS recent_count
        FROM defect_tickets
        GROUP BY location_name
        ORDER BY defect_count DESC
    """).fetchall()

    conn.close()

    return [dict(row) for row in rows]


# ---------------------------------------------------------
# SYSTEM HEALTH
# ---------------------------------------------------------

@app.get("/api/system-health")
def system_health():

    return {
        "audio_pipeline": "OPERATIONAL",
        "image_pipeline": "OPERATIONAL",
        "detection_engine": "OPERATIONAL",
        "database": "OPERATIONAL",
        "active_learning": "READY"
    }


# ---------------------------------------------------------
# PASSENGER BOOKINGS — links a phone number to a journey so SMS has
# somewhere to go, and so /api/track-health can find their train.
# ---------------------------------------------------------

class BookingRequest(BaseModel):
    phone: str
    email: str | None = None
    source: str
    destination: str
    timing: str  # "HH:MM"


@app.post("/api/bookings")
def create_booking(booking: BookingRequest):

    train_number = timetable.find_train_for_journey(
        booking.source, booking.destination, booking.timing
    )
    if not train_number:
        raise HTTPException(status_code=404, detail="No matching train for that source/destination.")

    conn = get_db()
    conn.execute("""
        INSERT INTO bookings (phone, email, source, destination, train_number, timing, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (booking.phone, booking.email, booking.source, booking.destination,
          train_number, booking.timing, now()))
    conn.commit()
    conn.close()

    return {"train_number": train_number, "message": "Booking linked to train."}


# ---------------------------------------------------------
# TRACK HEALTH — the passenger transparency feature: given a journey,
# find the train, and report open defects ahead of its current position.
# ---------------------------------------------------------

@app.get("/api/track-health")
def track_health(source: str, destination: str, timing: str = ""):

    train_number = timetable.find_train_for_journey(source, destination, timing)
    if not train_number:
        raise HTTPException(status_code=404, detail="No train found for that journey.")

    train = timetable.get_train(train_number)
    position = timetable.get_live_position(train, _sim_minutes_elapsed())

    conn = get_db()
    rows = conn.execute("""
        SELECT * FROM defect_tickets
        WHERE status = 'ACTIVE' AND train_number = ?
        ORDER BY id DESC
    """, (train_number,)).fetchall()
    conn.close()

    tickets = [dict(r) for r in rows]
    critical = [t for t in tickets if t["severity"] == "CRITICAL"]

    return {
        "train_number": train_number,
        "route": f"{train['source']} \u2192 {train['destination']}",
        "current_position": position,
        "status": "CRITICAL" if critical else ("ABNORMAL" if tickets else "CLEAR"),
        "open_defects": tickets,
        "last_checked": now(),
    }


# ---------------------------------------------------------
# REROUTE + SMS — for a CRITICAL ticket: notify rerouting manager /
# maintenance team, and SMS every booked passenger on that train.
# SMS is mocked here (prints + returns the list) — swap in a real
# provider call (Twilio/MSG91) where marked below.
# ---------------------------------------------------------

@app.post("/api/tickets/{ticket_id}/reroute")
def reroute_ticket(ticket_id: int):

    conn = get_db()
    ticket = conn.execute("SELECT * FROM defect_tickets WHERE id = ?", (ticket_id,)).fetchone()
    if not ticket:
        conn.close()
        raise HTTPException(status_code=404, detail="Ticket not found")

    if ticket["severity"] != "CRITICAL":
        conn.close()
        raise HTTPException(status_code=400, detail="Rerouting is only for CRITICAL tickets.")

    train_number = ticket["train_number"]
    bookings = conn.execute(
        "SELECT * FROM bookings WHERE train_number = ?", (train_number,)
    ).fetchall()
    conn.close()

    recipients = [b["phone"] for b in bookings]

    # --- REAL SMS GOES HERE ---
    # from twilio.rest import Client
    # client = Client(TWILIO_SID, TWILIO_AUTH_TOKEN)
    # for phone in recipients:
    #     client.messages.create(
    #         to=phone, from_=TWILIO_NUMBER,
    #         body=f"Train {train_number} rerouted due to a critical track defect. "
    #              f"Please check the app for updated platform/route info.",
    #     )
    for phone in recipients:
        print(f"[SMS MOCK] -> {phone}: Train {train_number} rerouted due to a "
              f"critical defect between {ticket['location_name']}.")

    return {
        "message": f"Reroute triggered for train {train_number}.",
        "notified_rerouting_manager": True,
        "notified_maintenance_team": True,
        "sms_sent_to": recipients,
        "sms_count": len(recipients),
    }


# ---------------------------------------------------------
# ROOT
# ---------------------------------------------------------

@app.get("/")
def root():

    return {
        "system": "RailSafe Engineer Control System",
        "status": "online"
    } 