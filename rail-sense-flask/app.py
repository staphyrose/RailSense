import os
import random
import threading
import time
import sqlite3
import hashlib
import secrets
from datetime import datetime
from io import BytesIO

from dotenv import load_dotenv

import joblib
import tensorflow as tf
from PIL import Image

import uuid
import librosa
import numpy as np

from flask import (
    Flask, render_template, request, jsonify, send_file,
    session, redirect
)
try:
    from twilio.rest import Client
except ImportError:
    Client = None
from scipy.signal import butter, sosfiltfilt


# ============================================================
# FLASK
# ============================================================

app = Flask(__name__)


# ============================================================
# PROJECT PATHS
# ============================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Load .env from the same folder as app.py
ENV_PATH = os.path.join(BASE_DIR, ".env")
load_dotenv(ENV_PATH)

# Signs the passenger session cookie. Set FLASK_SECRET_KEY in .env so
# passengers stay signed in across server restarts; without it a random
# key is generated on every start (passengers simply sign in again).
app.secret_key = os.getenv("FLASK_SECRET_KEY") or secrets.token_hex(32)

print("=" * 60)
print("TWILIO CONFIGURATION")
print("=" * 60)
print("Environment file:", ENV_PATH)
print("Account SID loaded:", bool(os.getenv("TWILIO_ACCOUNT_SID")))
print("Auth Token loaded:", bool(os.getenv("TWILIO_AUTH_TOKEN")))
print("From phone loaded:", bool(os.getenv("TWILIO_FROM_PHONE")))
print("Routing phone loaded:", bool(os.getenv("ROUTING_OFFICER_PHONE")))
print("=" * 60)

MODEL_DIR = os.path.join(BASE_DIR, "models")

SVM_MODEL_PATH = os.path.join(MODEL_DIR, "svm_model.pkl")
SVM_SCALER_PATH = os.path.join(MODEL_DIR, "svm_scaler.pkl")

AUDIO_TESTING_PATH = r"C:\Railsense_Main\testing_data"

IMAGE_TESTING_PATH = r"C:\Railsense_Main\image_testing_data"

IMAGE_MODEL_PATH = os.path.join(
    MODEL_DIR, "image", "ResNet50_improved_final.keras"
)

IMAGE_GRADCAM_DIR = os.path.join(BASE_DIR, "runtime_image_gradcam")
os.makedirs(IMAGE_GRADCAM_DIR, exist_ok=True)


# ============================================================
# REAL SVM MODEL
# ============================================================

try:
    svm_model = joblib.load(SVM_MODEL_PATH)
    scaler = joblib.load(SVM_SCALER_PATH)

    print("=" * 60)
    print("REAL SVM MODEL LOADED SUCCESSFULLY")
    print("=" * 60)
    print("Model :", SVM_MODEL_PATH)
    print("Scaler:", SVM_SCALER_PATH)
    print("=" * 60)

except Exception as error:
    svm_model = None
    scaler = None

    print("=" * 60)
    print("WARNING: SVM MODEL COULD NOT BE LOADED")
    print("=" * 60)
    print(error)
    print("=" * 60)


# ============================================================
# REAL RESNET50 IMAGE MODEL
# ============================================================

try:
    image_model = tf.keras.models.load_model(
        IMAGE_MODEL_PATH,
        compile=False
    )

    IMAGE_CLASS_NAMES = [
        "Cracks",
        "Flakings",
        "Grooves",
        "Joints",
        "Shellings",
        "Spallings",
        "Squats",
    ]

    print("=" * 60)
    print("REAL RESNET50 IMAGE MODEL LOADED SUCCESSFULLY")
    print("=" * 60)
    print("Model:", IMAGE_MODEL_PATH)
    print("Classes:", IMAGE_CLASS_NAMES)
    print("=" * 60)

except Exception as error:
    image_model = None
    IMAGE_CLASS_NAMES = [
        "Cracks", "Flakings", "Grooves", "Joints",
        "Shellings", "Spallings", "Squats"
    ]

    print("=" * 60)
    print("WARNING: IMAGE MODEL COULD NOT BE LOADED")
    print("=" * 60)
    print(error)
    print("=" * 60)


# ============================================================
# ENGINEER ACCESS
# ============================================================

ENGINEER_SECRET = "1234"


# ============================================================
# PERSISTENT DATABASE + TWILIO
# ============================================================

DATABASE_PATH = os.path.join(BASE_DIR, "railsense.db")

TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "").strip()
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "").strip()
TWILIO_FROM_PHONE = os.getenv("TWILIO_FROM_PHONE", "").strip()
ROUTING_OFFICER_PHONE = os.getenv("ROUTING_OFFICER_PHONE", "").strip()
TWILIO_WHATSAPP_FROM = os.getenv("TWILIO_WHATSAPP_FROM", "").strip()

# Twilio SMS free-trial accounts reject any custom message body.
# The Body parameter must instead be the literal KEYWORD of one of
# Twilio's own pre-approved templates (confirmed via Console ->
# Messaging -> Try it out -> SMS -> API tab), e.g. Body=
# "sms_appointment_reminders". Twilio resolves that keyword into the
# real approved text server-side before delivering it - sending the
# rendered sentence itself fails with "Invalid template name."
# Set TWILIO_TRIAL_MODE=false once the account is upgraded to send
# the real RailSense message text instead.
TWILIO_TRIAL_MODE = (
    os.getenv("TWILIO_TRIAL_MODE", "true").strip().lower() != "false"
)

# Keyword for the passenger-facing template. "sms_appointment_reminders"
# fits a scheduled-maintenance-date message reasonably well. Change via
# env var if you prefer a different confirmed-working category, e.g.
# "sms_event_notifications".
TWILIO_TRIAL_TEMPLATE_PASSENGERS = os.getenv(
    "TWILIO_TRIAL_TEMPLATE_PASSENGERS", "sms_appointment_reminders"
).strip()

# Keyword for the routing-officer/internal-ops template.
TWILIO_TRIAL_TEMPLATE_ROUTING = os.getenv(
    "TWILIO_TRIAL_TEMPLATE_ROUTING", "sms_event_notifications"
).strip()

TICKETS = []
NOTIFICATIONS = []

TICKET_COUNTER = 0
DATABASE_LOCK = threading.Lock()


def get_db():
    conn = sqlite3.connect(DATABASE_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def init_database():
    conn = get_db()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                identifier TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                name TEXT NOT NULL,
                phone TEXT NOT NULL,
                sms_alerts INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            )
        """)
        # The train a passenger has chosen to travel on. Written by the
        # future "My Journey" feature; the dashboard already reads it.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS passenger_journeys (
                user_id INTEGER PRIMARY KEY,
                train_number TEXT NOT NULL,
                selected_at TEXT NOT NULL
            )
        """)
        # My Journey also remembers the chosen source / destination.
        existing = {r["name"] for r in conn.execute("PRAGMA table_info(passenger_journeys)")}
        if "source_station" not in existing:
            conn.execute("ALTER TABLE passenger_journeys ADD COLUMN source_station TEXT")
        if "destination_station" not in existing:
            conn.execute("ALTER TABLE passenger_journeys ADD COLUMN destination_station TEXT")
        # Safety Alerts: which alerts a passenger has already opened.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS passenger_alert_reads (
                user_id INTEGER NOT NULL,
                alert_key TEXT NOT NULL,
                read_at TEXT NOT NULL,
                PRIMARY KEY (user_id, alert_key)
            )
        """)
        # Passenger Chat Room: one room per train.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS passenger_chat_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                train_number TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                sender_name TEXT NOT NULL,
                body TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_chat_room ON passenger_chat_messages (train_number, id)"
        )
        # Report Issue / My Reports: passenger observations shared with the
        # Engineer dashboard. Kept apart from the AI tickets on purpose, so a
        # passenger can never create or change an engineer ticket.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS passenger_reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT NOT NULL DEFAULT '',
                user_id INTEGER NOT NULL,
                passenger_name TEXT NOT NULL,
                train_number TEXT NOT NULL,
                train_name TEXT NOT NULL,
                location TEXT NOT NULL,
                issue_type TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'Submitted',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_reports_user ON passenger_reports (user_id, id)"
        )
        conn.commit()
    finally:
        conn.close()


def hash_password(password):
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        120000
    ).hex()
    return f"{salt}${digest}"


def verify_password(password, stored):
    try:
        salt, expected = stored.split("$", 1)
        actual = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt.encode("utf-8"),
            120000
        ).hex()
        return secrets.compare_digest(actual, expected)
    except Exception:
        return False


def normalize_phone(phone):
    phone = str(phone or "").strip().replace(" ", "").replace("-", "")
    if phone.startswith("+"):
        return phone
    if phone.startswith("91") and len(phone) == 12:
        return "+" + phone
    if len(phone) == 10:
        return "+91" + phone
    return phone


def twilio_client():
    if Client is None:
        raise RuntimeError(
            "Twilio is not installed. Run: pip install twilio"
        )
    if not TWILIO_ACCOUNT_SID or not TWILIO_AUTH_TOKEN:
        raise RuntimeError(
            "Twilio credentials are missing. Set TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN."
        )
    return Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)


def send_sms(to_phone, message, template_keyword=TWILIO_TRIAL_TEMPLATE_PASSENGERS):
    if not TWILIO_FROM_PHONE:
        raise RuntimeError(
            "TWILIO_FROM_PHONE is not configured."
        )

    normalized_phone = normalize_phone(to_phone)

    if not normalized_phone:
        raise RuntimeError(
            "Passenger phone number is empty."
        )

    # The real RailSense message (with defect location, maintenance
    # date, etc.) is always stored in NOTIFICATIONS for the dashboard.
    # While TWILIO_TRIAL_MODE is on, the Body sent to Twilio must be
    # the literal KEYWORD of one of Twilio's approved trial templates
    # (e.g. "sms_appointment_reminders") - Twilio resolves it into the
    # real approved text server-side. Sending your own sentence fails
    # with "Invalid template name."
    body_to_send = (
        template_keyword if TWILIO_TRIAL_MODE else message
    )

    print(
        f"Attempting SMS: {TWILIO_FROM_PHONE} -> {normalized_phone}"
    )

    if TWILIO_TRIAL_MODE:
        print(
            f"TWILIO_TRIAL_MODE is on: sending template keyword "
            f"'{template_keyword}' instead of the custom RailSense "
            f"text. Set TWILIO_TRIAL_MODE=false after upgrading."
        )

    client = twilio_client()

    result = client.messages.create(
        body=body_to_send,
        from_=TWILIO_FROM_PHONE,
        to=normalized_phone
    )

    print(
        f"SMS successfully submitted to Twilio. SID: {result.sid}"
    )

    return result.sid


def send_whatsapp(to_phone, message):
    if not TWILIO_WHATSAPP_FROM:
        return None
    client = twilio_client()
    result = client.messages.create(
        body=message,
        from_=(
            TWILIO_WHATSAPP_FROM
            if TWILIO_WHATSAPP_FROM.startswith("whatsapp:")
            else "whatsapp:" + TWILIO_WHATSAPP_FROM
        ),
        to="whatsapp:" + normalize_phone(to_phone)
    )
    return result.sid


init_database()


# ============================================================
# SIMULATION STATE
# ============================================================

SIMULATION_RUNNING = False
SIMULATION_THREAD = None
SIMULATION_LOCK = threading.Lock()


# ============================================================
# 8 RAILSENSE EXPRESS TRAINS
# ============================================================

TRAINS = [
    {
        "train_number": "12951",
        "name": "Mumbai Central - New Delhi Rajdhani Express",
        "from_station": "Mumbai Central",
        "to_station": "New Delhi",
        "corridor": "Mumbai Central → New Delhi",
        "route": [
            {"name": "Mumbai Central", "lat": 18.9690, "lon": 72.8194},
            {"name": "Surat", "lat": 21.1702, "lon": 72.8311},
            {"name": "Vadodara", "lat": 22.3072, "lon": 73.1812},
            {"name": "Ratlam", "lat": 23.3315, "lon": 75.0367},
            {"name": "Kota", "lat": 25.2138, "lon": 75.8648},
            {"name": "New Delhi", "lat": 28.6139, "lon": 77.2090},
        ],
    },
    {
        "train_number": "12953",
        "name": "Mumbai Central - August Kranti Rajdhani Express",
        "from_station": "Mumbai Central",
        "to_station": "Hazrat Nizamuddin",
        "corridor": "Mumbai Central → Hazrat Nizamuddin",
        "route": [
            {"name": "Mumbai Central", "lat": 18.9690, "lon": 72.8194},
            {"name": "Surat", "lat": 21.1702, "lon": 72.8311},
            {"name": "Vadodara", "lat": 22.3072, "lon": 73.1812},
            {"name": "Ratlam", "lat": 23.3315, "lon": 75.0367},
            {"name": "Kota", "lat": 25.2138, "lon": 75.8648},
            {"name": "Hazrat Nizamuddin", "lat": 28.5880, "lon": 77.2500},
        ],
    },
    {
        "train_number": "12925",
        "name": "Paschim Express",
        "from_station": "Mumbai Central",
        "to_station": "Amritsar",
        "corridor": "Mumbai Central → Amritsar",
        "route": [
            {"name": "Mumbai Central", "lat": 18.9690, "lon": 72.8194},
            {"name": "Surat", "lat": 21.1702, "lon": 72.8311},
            {"name": "Vadodara", "lat": 22.3072, "lon": 73.1812},
            {"name": "Ratlam", "lat": 23.3315, "lon": 75.0367},
            {"name": "Delhi", "lat": 28.6139, "lon": 77.2090},
            {"name": "Ludhiana", "lat": 30.9010, "lon": 75.8573},
            {"name": "Amritsar", "lat": 31.6340, "lon": 74.8723},
        ],
    },
    {
        "train_number": "12903",
        "name": "Golden Temple Mail",
        "from_station": "Mumbai Central",
        "to_station": "Amritsar",
        "corridor": "Mumbai Central → Amritsar",
        "route": [
            {"name": "Mumbai Central", "lat": 18.9690, "lon": 72.8194},
            {"name": "Surat", "lat": 21.1702, "lon": 72.8311},
            {"name": "Vadodara", "lat": 22.3072, "lon": 73.1812},
            {"name": "Ratlam", "lat": 23.3315, "lon": 75.0367},
            {"name": "Kota", "lat": 25.2138, "lon": 75.8648},
            {"name": "Delhi", "lat": 28.6139, "lon": 77.2090},
            {"name": "Ludhiana", "lat": 30.9010, "lon": 75.8573},
            {"name": "Amritsar", "lat": 31.6340, "lon": 74.8723},
        ],
    },
    {
        "train_number": "12909",
        "name": "Mumbai Central - Ahmedabad Shatabdi Express",
        "from_station": "Mumbai Central",
        "to_station": "Ahmedabad",
        "corridor": "Mumbai Central → Ahmedabad",
        "route": [
            {"name": "Mumbai Central", "lat": 18.9690, "lon": 72.8194},
            {"name": "Surat", "lat": 21.1702, "lon": 72.8311},
            {"name": "Vadodara", "lat": 22.3072, "lon": 73.1812},
            {"name": "Anand", "lat": 22.5645, "lon": 72.9289},
            {"name": "Ahmedabad", "lat": 23.0225, "lon": 72.5714},
        ],
    },
    {
        "train_number": "12935",
        "name": "Bandra Terminus - Surat Intercity Express",
        "from_station": "Bandra Terminus",
        "to_station": "Surat",
        "corridor": "Bandra Terminus → Surat",
        "route": [
            {"name": "Bandra Terminus", "lat": 19.0625, "lon": 72.8406},
            {"name": "Vapi", "lat": 20.3710, "lon": 72.9049},
            {"name": "Surat", "lat": 21.1702, "lon": 72.8311},
        ],
    },
    {
        "train_number": "12927",
        "name": "Mumbai Central - Vadodara Express",
        "from_station": "Mumbai Central",
        "to_station": "Vadodara",
        "corridor": "Mumbai Central → Vadodara",
        "route": [
            {"name": "Mumbai Central", "lat": 18.9690, "lon": 72.8194},
            {"name": "Vapi", "lat": 20.3710, "lon": 72.9049},
            {"name": "Surat", "lat": 21.1702, "lon": 72.8311},
            {"name": "Vadodara", "lat": 22.3072, "lon": 73.1812},
        ],
    },
    {
        "train_number": "12955",
        "name": "Mumbai Central - Jaipur Superfast Express",
        "from_station": "Mumbai Central",
        "to_station": "Jaipur",
        "corridor": "Mumbai Central → Jaipur",
        "route": [
            {"name": "Mumbai Central", "lat": 18.9690, "lon": 72.8194},
            {"name": "Surat", "lat": 21.1702, "lon": 72.8311},
            {"name": "Vadodara", "lat": 22.3072, "lon": 73.1812},
            {"name": "Ratlam", "lat": 23.3315, "lon": 75.0367},
            {"name": "Kota", "lat": 25.2138, "lon": 75.8648},
            {"name": "Sawai Madhopur", "lat": 26.0173, "lon": 76.3500},
            {"name": "Jaipur", "lat": 26.9124, "lon": 75.7873},
        ],
    },
]


# ============================================================
# LIVE TRAIN STATE
# ============================================================

TRAIN_STATE = {}

for train in TRAINS:
    TRAIN_STATE[train["train_number"]] = {
        "train_number": train["train_number"],
        "name": train["name"],
        "from_station": train["from_station"],
        "to_station": train["to_station"],
        "corridor": train["corridor"],
        "route": train["route"],
        "route_index": 0,
        "position_fraction": 0.0,
        "latitude": train["route"][0]["lat"],
        "longitude": train["route"][0]["lon"],
        "location_name": train["route"][0]["name"],
        "modality": "WAITING",
        "prediction": "Normal",
        "severity": "Normal",
        "confidence": 0.0,
        "last_file": None,
        "last_inspection": None,
        "status": "Monitoring",
    }


# ============================================================
# CLASS / SEVERITY MAPPING
# ============================================================

SVM_CLASS_MAP = {
    0: "Normal",
    1: "Superelivation",
    2: "Wheelburnt",
}

OPERATIONAL_CLASS_MAP = {
    "Normal": "Normal",
    "Superelivation": "Abnormal",
    "Wheelburnt": "Critical",
}


def operational_severity(defect_name):
    return OPERATIONAL_CLASS_MAP.get(defect_name, "Abnormal")


IMAGE_SEVERITY_MAP = {
    "Cracks": "Critical",
    "Squats": "Critical",
    "Flakings": "Abnormal",
    "Grooves": "Abnormal",
    "Joints": "Abnormal",
    "Shellings": "Abnormal",
    "Spallings": "Abnormal",
}


def image_operational_severity(defect_name):
    return IMAGE_SEVERITY_MAP.get(defect_name, "Abnormal")


# ============================================================
# AUDIO FILE DISCOVERY
# ============================================================

def get_audio_files():
    files = []

    if not os.path.exists(AUDIO_TESTING_PATH):
        return files

    for root, _, filenames in os.walk(AUDIO_TESTING_PATH):
        for filename in filenames:
            if filename.lower().endswith(".wav"):
                files.append(os.path.join(root, filename))

    return files


def choose_random_audio():
    files = get_audio_files()
    return random.choice(files) if files else None


# ============================================================
# IMAGE FILE DISCOVERY
# ============================================================

# Cache valid images so the dashboard polling does not repeatedly open every file.
IMAGE_FILES_CACHE = None
IMAGE_FILES_CACHE_LOCK = threading.Lock()
INVALID_IMAGE_FILES = set()


def is_valid_image_file(image_path):
    """Return True only when Pillow can verify the file as a real image."""
    try:
        with Image.open(image_path) as image:
            image.verify()
        return True
    except Exception as error:
        if image_path not in INVALID_IMAGE_FILES:
            INVALID_IMAGE_FILES.add(image_path)
            print(
                "Skipping invalid image:",
                image_path,
                "|",
                str(error)
            )
        return False


def get_image_files(force_refresh=False):
    """Discover only valid image files from the image testing directory."""
    global IMAGE_FILES_CACHE

    with IMAGE_FILES_CACHE_LOCK:
        if IMAGE_FILES_CACHE is not None and not force_refresh:
            return list(IMAGE_FILES_CACHE)

        valid_files = []

        if not os.path.exists(IMAGE_TESTING_PATH):
            IMAGE_FILES_CACHE = []
            return []

        valid_extensions = (".jpg", ".jpeg", ".png", ".bmp", ".webp")

        for root, _, filenames in os.walk(IMAGE_TESTING_PATH):
            for filename in filenames:
                if not filename.lower().endswith(valid_extensions):
                    continue

                image_path = os.path.join(root, filename)

                if is_valid_image_file(image_path):
                    valid_files.append(image_path)

        IMAGE_FILES_CACHE = valid_files

        print(
            f"Image validation complete: {len(valid_files)} valid image(s), "
            f"{len(INVALID_IMAGE_FILES)} invalid image(s) skipped."
        )

        return list(IMAGE_FILES_CACHE)


def choose_random_image():
    """Choose only from validated image files."""
    files = get_image_files()
    return random.choice(files) if files else None


# ============================================================
# BUTTERWORTH FILTER
# ============================================================

def butterworth_filter(audio, sr, lowcut=50, highcut=3500, order=5):
    nyquist = sr / 2
    highcut = min(highcut, nyquist * 0.95)

    sos = butter(
        order,
        [lowcut, highcut],
        btype="bandpass",
        fs=sr,
        output="sos",
    )

    return sosfiltfilt(sos, audio)


# ============================================================
# REAL SVM PREDICTION
# ============================================================

def predict_audio_file(audio_path):
    if svm_model is None or scaler is None:
        raise RuntimeError("SVM model or scaler is not loaded.")

    audio, sr = librosa.load(audio_path, sr=None)

    filtered_audio = butterworth_filter(
        audio, sr, lowcut=50, highcut=3500
    )

    mfcc = librosa.feature.mfcc(
        y=filtered_audio,
        sr=sr,
        n_mfcc=40,
        n_mels=128,
    )

    mfcc_features = np.mean(mfcc, axis=1)
    X_input = mfcc_features.reshape(1, -1)
    X_input_scaled = scaler.transform(X_input)

    prediction = svm_model.predict(X_input_scaled)
    predicted_value = int(prediction[0])

    defect_name = SVM_CLASS_MAP.get(predicted_value, "Normal")
    severity = operational_severity(defect_name)

    probabilities = {}

    if hasattr(svm_model, "predict_proba"):
        probability_values = svm_model.predict_proba(X_input_scaled)[0]

        for index, probability in enumerate(probability_values):
            model_class = svm_model.classes_[index]

            try:
                model_class = int(model_class)
            except Exception:
                pass

            class_name = SVM_CLASS_MAP.get(
                model_class, str(model_class)
            )

            probabilities[class_name] = float(probability)

        confidence = float(np.max(probability_values))
    else:
        confidence = 1.0

    return {
        "svm_class": defect_name,
        "severity": severity,
        "confidence": confidence,
        "probabilities": probabilities,
        "sample_rate": sr,
        "samples": len(audio),
        "duration": float(len(audio) / sr),
    }


# ============================================================
# LOG-MEL SPECTROGRAM
# ============================================================

def create_log_mel_spectrogram(audio_path):
    audio, sr = librosa.load(audio_path, sr=None)

    filtered_audio = butterworth_filter(
        audio, sr, lowcut=50, highcut=3500
    )

    mel_spec = librosa.feature.melspectrogram(
        y=filtered_audio,
        sr=sr,
        n_mels=128,
        n_fft=1024,
        hop_length=512,
        power=2.0,
    )

    log_mel = librosa.power_to_db(mel_spec, ref=np.max)

    # Use Pillow so no matplotlib display is required.
    normalized = (
        (log_mel - log_mel.min())
        / (log_mel.max() - log_mel.min() + 1e-9)
        * 255
    ).astype(np.uint8)

    normalized = np.flipud(normalized)

    from PIL import Image

    image = Image.fromarray(normalized, mode="L")

    target_width = 900

    if image.width > 0:
        target_height = max(
            220,
            int(image.height * target_width / image.width),
        )

        image = image.resize((target_width, target_height))

    output = BytesIO()
    image.save(output, format="PNG")
    output.seek(0)

    return output


# ============================================================
# AUDIO WAVEFORM IMAGE
# ============================================================

def create_waveform_image(audio_path):
    from PIL import Image, ImageDraw

    audio, sr = librosa.load(audio_path, sr=None)

    filtered_audio = butterworth_filter(
        audio, sr, lowcut=50, highcut=3500
    )

    width, height = 900, 260
    margin = 10

    # Downsample to one min/max pair per pixel column so the
    # shape of the waveform is preserved regardless of length.
    plot_width = width - (margin * 2)
    samples_per_column = max(1, len(filtered_audio) // plot_width)

    peaks = []
    for column in range(plot_width):
        start = column * samples_per_column
        end = start + samples_per_column

        chunk = filtered_audio[start:end]

        if len(chunk) == 0:
            peaks.append((0.0, 0.0))
        else:
            peaks.append((float(np.min(chunk)), float(np.max(chunk))))

    max_abs = max(
        1e-6,
        max(abs(lo) for lo, _ in peaks),
        max(abs(hi) for _, hi in peaks),
    )

    image = Image.new("RGB", (width, height), color=(8, 8, 9))
    draw = ImageDraw.Draw(image)

    center_y = height // 2

    # Faint horizontal gridlines for readability.
    for fraction in (0.25, 0.5, 0.75):
        y = int(height * fraction)
        draw.line(
            [(0, y), (width, y)],
            fill=(30, 30, 34),
            width=1,
        )

    draw.line(
        [(0, center_y), (width, center_y)],
        fill=(50, 50, 56),
        width=1,
    )

    waveform_color = (56, 189, 248)  # bright cyan, clearly visible

    for column, (lo, hi) in enumerate(peaks):
        x = margin + column

        y_top = center_y - int(
            (hi / max_abs) * (center_y - margin)
        )
        y_bottom = center_y - int(
            (lo / max_abs) * (center_y - margin)
        )

        if y_top == y_bottom:
            y_bottom += 1

        draw.line(
            [(x, y_top), (x, y_bottom)],
            fill=waveform_color,
            width=1,
        )

    output = BytesIO()
    image.save(output, format="PNG")
    output.seek(0)

    return output


# ============================================================
# MFCC VISUALIZATION IMAGE
# ============================================================

def create_mfcc_image(audio_path):
    from PIL import Image

    audio, sr = librosa.load(audio_path, sr=None)

    filtered_audio = butterworth_filter(
        audio, sr, lowcut=50, highcut=3500
    )

    mfcc = librosa.feature.mfcc(
        y=filtered_audio,
        sr=sr,
        n_mfcc=40,
        n_mels=128,
    )

    normalized = (mfcc - mfcc.min()) / (mfcc.max() - mfcc.min() + 1e-9)

    # Simple blue -> cyan -> yellow -> red colour ramp so the
    # coefficients are easy to read (avoids a flat grayscale look).
    stops = np.array([
        [13, 12, 60],
        [37, 84, 168],
        [56, 189, 248],
        [250, 204, 21],
        [239, 68, 68],
    ], dtype=np.float32)

    positions = np.linspace(0.0, 1.0, len(stops))

    flat = normalized.reshape(-1)
    red = np.interp(flat, positions, stops[:, 0])
    green = np.interp(flat, positions, stops[:, 1])
    blue = np.interp(flat, positions, stops[:, 2])

    rgb = np.stack([red, green, blue], axis=-1)
    rgb = rgb.reshape(normalized.shape[0], normalized.shape[1], 3)
    rgb = np.flipud(rgb).astype(np.uint8)

    image = Image.fromarray(rgb, mode="RGB")

    target_width = 900

    if image.width > 0:
        target_height = max(
            220,
            int(image.height * target_width / image.width),
        )

        image = image.resize((target_width, target_height))

    output = BytesIO()
    image.save(output, format="PNG")
    output.seek(0)

    return output


# ============================================================
# ROUTE POSITION
# ============================================================

def get_position_on_route(train):
    route = train["route"]
    route_index = train["route_index"]
    position_fraction = train["position_fraction"]

    if route_index >= len(route) - 1:
        return (
            route[-1]["lat"],
            route[-1]["lon"],
            route[-1]["name"],
        )

    start = route[route_index]
    end = route[route_index + 1]

    latitude = start["lat"] + (
        end["lat"] - start["lat"]
    ) * position_fraction

    longitude = start["lon"] + (
        end["lon"] - start["lon"]
    ) * position_fraction

    location_name = f'{start["name"]} → {end["name"]}'

    return latitude, longitude, location_name


# ============================================================
# REAL IMAGE PREDICTION + GRAD-CAM
# ============================================================

def load_image_for_model(image_path):
    """Load a validated image using the same raw 0-255 input expected by the model."""
    try:
        with Image.open(image_path) as image:
            image.load()
            image = image.convert("RGB")
            image = image.resize((224, 224))
            array = np.asarray(image, dtype=np.float32)
    except Exception as error:
        raise ValueError(
            f"Invalid or unreadable image file: {image_path}. {error}"
        ) from error

    return np.expand_dims(array, axis=0)


def generate_gradcam(image_array, predicted_index, output_path):
    """Generate Grad-CAM using the actual nested ResNet50 graph.

    The saved model contains ResNet50 as a nested model and the classifier
    layers outside it. Connecting the nested convolution output directly to
    image_model.inputs creates a disconnected Keras graph, so we rebuild the
    forward path from the ResNet50 input through the real classifier head.
    """
    if image_model is None:
        raise RuntimeError("Image model is not loaded.")

    try:
        base_model = image_model.get_layer("resnet50")
    except Exception as error:
        raise RuntimeError("Nested ResNet50 backbone was not found in the image model.") from error

    try:
        last_conv_layer = base_model.get_layer("conv5_block3_3_conv")
    except Exception:
        conv_layers = [
            layer for layer in base_model.layers
            if isinstance(layer, tf.keras.layers.Conv2D)
        ]
        if not conv_layers:
            raise RuntimeError("No convolution layer found for Grad-CAM.")
        last_conv_layer = conv_layers[-1]

    # Build a connected graph starting from the actual ResNet50 input.
    # The outer model already contains ImageNet preprocessing, so apply the
    # same preprocessing here before feeding the backbone.
    raw_input = tf.keras.Input(
        shape=base_model.input_shape[1:],
        name="gradcam_input"
    )
    preprocessed_input = tf.keras.applications.resnet50.preprocess_input(raw_input)

    x = preprocessed_input

    # Build a single submodel that yields BOTH the conv-layer activation
    # and the full backbone output from the SAME forward pass. Getting
    # them from two separate calls (as before) produces two independent
    # computation graphs sharing only weights, not tensors - so there is
    # no path for gradients to flow from base_output back to conv_outputs.
    base_model_multi = tf.keras.models.Model(
        inputs=base_model.input,
        outputs=[last_conv_layer.output, base_model.output]
    )

    conv_outputs, base_output = base_model_multi(x, training=False)

    # Recreate the classifier head exactly from the trained outer model.
    base_index = None
    for index, layer in enumerate(image_model.layers):
        if layer is base_model:
            base_index = index
            break

    if base_index is None:
        raise RuntimeError("Could not locate ResNet50 in the outer model.")

    x = base_output
    head_layers = image_model.layers[base_index + 1:]

    for layer in head_layers:
        # Skip an InputLayer if a model format exposes one after the backbone.
        if isinstance(layer, tf.keras.layers.InputLayer):
            continue
        x = layer(x, training=False) if hasattr(layer, "training") else layer(x)

    grad_model = tf.keras.models.Model(
        inputs=raw_input,
        outputs=[conv_outputs, x]
    )

    # image_array contains raw 0-255 RGB pixels, matching normal inference.
    input_tensor = tf.convert_to_tensor(image_array, dtype=tf.float32)

    with tf.GradientTape() as tape:
        conv_outputs_value, predictions = grad_model(input_tensor, training=False)
        class_score = predictions[:, predicted_index]

    gradients = tape.gradient(class_score, conv_outputs_value)

    if gradients is None:
        raise RuntimeError("Grad-CAM gradients could not be calculated.")

    pooled_gradients = tf.reduce_mean(gradients, axis=(1, 2))
    conv_outputs_value = conv_outputs_value[0]
    pooled_gradients = pooled_gradients[0]

    heatmap = tf.reduce_sum(
        conv_outputs_value * pooled_gradients[tf.newaxis, tf.newaxis, :],
        axis=-1
    )
    heatmap = tf.maximum(heatmap, 0)
    max_value = tf.reduce_max(heatmap)

    heatmap = tf.where(
        max_value > 0,
        heatmap / max_value,
        tf.zeros_like(heatmap)
    )

    heatmap_uint8 = np.uint8(255 * heatmap.numpy())
    original = Image.fromarray(
        np.uint8(image_array[0])
    ).convert("RGB")

    # Create a simple red/yellow attention map without matplotlib.
    heatmap_rgb = np.zeros(
        (heatmap_uint8.shape[0], heatmap_uint8.shape[1], 3),
        dtype=np.uint8
    )
    heatmap_rgb[:, :, 0] = heatmap_uint8
    heatmap_rgb[:, :, 1] = np.uint8(
        np.clip(heatmap_uint8.astype(np.float32) * 0.55, 0, 255)
    )

    heatmap_rgb = Image.fromarray(heatmap_rgb).resize(original.size)
    overlay = Image.blend(original, heatmap_rgb, alpha=0.45)
    overlay.save(output_path, format="PNG")

def predict_image_file(image_path):
    if image_model is None:
        raise RuntimeError("ResNet50 image model is not loaded.")

    image_array = load_image_for_model(image_path)
    predictions = image_model.predict(image_array, verbose=0)[0]

    predicted_index = int(np.argmax(predictions))
    defect_name = IMAGE_CLASS_NAMES[predicted_index]
    confidence = float(predictions[predicted_index])
    severity = image_operational_severity(defect_name)

    probabilities = {
        IMAGE_CLASS_NAMES[i]: float(predictions[i])
        for i in range(min(len(IMAGE_CLASS_NAMES), len(predictions)))
    }

    gradcam_name = f"gradcam_{uuid.uuid4().hex}.png"
    gradcam_path = os.path.join(IMAGE_GRADCAM_DIR, gradcam_name)
    generate_gradcam(image_array, predicted_index, gradcam_path)

    return {
        "image_class": defect_name,
        "severity": severity,
        "confidence": confidence,
        "probabilities": probabilities,
        "gradcam_file": gradcam_name,
        "image_size": [224, 224],
    }


# ============================================================
# MOVE TRAIN
# ============================================================

def move_train(train):
    route = train["route"]

    if len(route) < 2:
        return

    train["position_fraction"] += 0.12

    if train["position_fraction"] >= 1.0:
        train["position_fraction"] = 0.0

        if train["route_index"] < len(route) - 2:
            train["route_index"] += 1
        else:
            train["route_index"] = 0

    (
        train["latitude"],
        train["longitude"],
        train["location_name"],
    ) = get_position_on_route(train)


# ============================================================
# TICKET ID
# ============================================================

def generate_ticket_id():
    global TICKET_COUNTER

    with DATABASE_LOCK:
        TICKET_COUNTER += 1
        return TICKET_COUNTER


# ============================================================
# TICKET CREATION
# ============================================================

def create_ticket(
    train,
    modality,
    prediction,
    severity,
    confidence,
    evidence_file=None,
    gradcam_file=None,
):
    ticket_id = generate_ticket_id()

    ticket = {
        "id": ticket_id,
        "train_number": train["train_number"],
        "train_name": train["name"],
        "from_station": train["from_station"],
        "to_station": train["to_station"],
        "corridor_name": train["corridor"],
        "source": modality,
        "defect_type": prediction,
        "severity": severity,
        "confidence": float(confidence),
        "latitude": float(train["latitude"]),
        "longitude": float(train["longitude"]),
        "location_name": train["location_name"],
        "position_fraction": float(
            (
                train["route_index"]
                + train["position_fraction"]
            )
            / max(len(train["route"]) - 1, 1)
        ),
        "route": train["route"],
        "evidence_file": (
            os.path.basename(evidence_file)
            if evidence_file
            else None
        ),
        "gradcam_file": gradcam_file,
        "status": "Active",
        "created_at": datetime.now().isoformat(),
        "passengers_informed": False,
        "routing_officer_informed": False,
    }

    with DATABASE_LOCK:
        TICKETS.append(ticket)

    return ticket


# ============================================================
# CRITICAL NOTIFICATION
# ============================================================

def create_critical_notification(ticket, notification_type):
    if notification_type == "passengers":
        message = (
            f'Safety alert: Train {ticket["train_number"]} '
            f'({ticket["train_name"]}) has detected a critical '
            f'defect near {ticket["location_name"]}. '
            f'Passengers should follow updated railway instructions.'
        )
        target = "Passengers"
    else:
        message = (
            f'CRITICAL DEFECT: Train {ticket["train_number"]} '
            f'has detected {ticket["defect_type"]} near '
            f'{ticket["location_name"]}. Routing intervention required.'
        )
        target = "Routing Officer"

    with DATABASE_LOCK:
        notification = {
            "id": len(NOTIFICATIONS) + 1,
            "ticket_id": ticket["id"],
            "type": notification_type,
            "target": target,
            "message": message,
            "status": "SIMULATED_SENT",
            "created_at": datetime.now().isoformat(),
        }
        NOTIFICATIONS.append(notification)

    return notification


# ============================================================
# INSPECT ONE TRAIN
# ============================================================

def inspect_train(train):
    modality = random.choice(["AUDIO", "IMAGE"])

    train["modality"] = modality
    train["last_inspection"] = datetime.now().isoformat()

    # --------------------------------------------------------
    # AUDIO = REAL SVM
    # --------------------------------------------------------

    if modality == "AUDIO":
        audio_file = choose_random_audio()

        if not audio_file:
            train["prediction"] = "Normal"
            train["severity"] = "Normal"
            train["confidence"] = 0.0
            train["status"] = "Audio files unavailable"
            return

        try:
            result = predict_audio_file(audio_file)

            prediction = result["svm_class"]
            severity = result["severity"]
            confidence = result["confidence"]

            train["prediction"] = prediction
            train["severity"] = severity
            train["confidence"] = confidence
            train["last_file"] = os.path.basename(audio_file)

            if severity == "Normal":
                train["status"] = "Monitoring"

                # Normal detections are visible in train state only.
                # No ticket is created for Normal.

            elif severity == "Abnormal":
                train["status"] = "Abnormal condition detected"

                create_ticket(
                    train=train,
                    modality="AUDIO",
                    prediction=prediction,
                    severity=severity,
                    confidence=confidence,
                    evidence_file=audio_file,
                )

            elif severity == "Critical":
                train["status"] = "CRITICAL DEFECT"

                create_ticket(
                    train=train,
                    modality="AUDIO",
                    prediction=prediction,
                    severity=severity,
                    confidence=confidence,
                    evidence_file=audio_file,
                )

        except Exception as error:
            print("Audio inspection error:", error)

            train["prediction"] = "Normal"
            train["severity"] = "Normal"
            train["confidence"] = 0.0
            train["status"] = "Audio inspection error"

    # --------------------------------------------------------
    # IMAGE = REAL RESNET50 + GRAD-CAM
    # --------------------------------------------------------
    else:
        image_file = choose_random_image()

        if not image_file:
            train["prediction"] = "Image files unavailable"
            train["severity"] = "Normal"
            train["confidence"] = 0.0
            train["last_file"] = None
            train["status"] = "Image files unavailable"
            return

        try:
            result = predict_image_file(image_file)

            prediction = result["image_class"]
            severity = result["severity"]
            confidence = result["confidence"]

            train["prediction"] = prediction
            train["severity"] = severity
            train["confidence"] = confidence
            train["last_file"] = os.path.basename(image_file)

            if severity == "Abnormal":
                train["status"] = "Abnormal condition detected"
                create_ticket(
                    train=train,
                    modality="IMAGE",
                    prediction=prediction,
                    severity=severity,
                    confidence=confidence,
                    evidence_file=image_file,
                    gradcam_file=result["gradcam_file"],
                )

            elif severity == "Critical":
                train["status"] = "CRITICAL DEFECT"
                ticket = create_ticket(
                    train=train,
                    modality="IMAGE",
                    prediction=prediction,
                    severity=severity,
                    confidence=confidence,
                    evidence_file=image_file,
                    gradcam_file=result["gradcam_file"],
                )

                # Notifications are intentionally NOT sent automatically.
                # The engineer must explicitly choose the recipient action.

        except Exception as error:
            print("Image inspection error:", error)
            train["prediction"] = "Image inspection error"
            train["severity"] = "Normal"
            train["confidence"] = 0.0
            train["status"] = "Image inspection error"


# ============================================================
# SIMULATION CYCLE
# ============================================================

def simulation_tick():
    for train_number in list(TRAIN_STATE.keys()):
        train = TRAIN_STATE[train_number]

        move_train(train)
        inspect_train(train)


# ============================================================
# BACKGROUND SIMULATION
# ============================================================

def simulation_worker():
    global SIMULATION_RUNNING

    print("RailSense live inspection worker started.")

    while True:
        with SIMULATION_LOCK:
            running = SIMULATION_RUNNING

        if not running:
            break

        try:
            simulation_tick()
        except Exception as error:
            print("Simulation cycle error:", error)

        time.sleep(4)

    print("RailSense live inspection worker stopped.")


# ============================================================
# TRAIN SERIALIZATION
# ============================================================

def get_all_trains():
    with SIMULATION_LOCK:
        return [dict(train) for train in TRAIN_STATE.values()]


# ============================================================
# ENGINEER LOGIN PAGE
#
# THIS ROUTE WAS MISSING IN THE PREVIOUS VERSION.
# That was the direct cause of:
# GET /engineer-login -> 404
# ============================================================

@app.route("/engineer-login")
@app.route("/engineer-login.html")
def engineer_login():
    template_path = os.path.join(
        BASE_DIR, "templates", "engineer-login.html"
    )

    # If the user's existing login HTML exists, use it.
    if os.path.exists(template_path):
        return render_template("engineer-login.html")

    # Otherwise provide a working fallback login page so the route
    # can never fail just because engineer-login.html is missing.
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>RailSense Engineer Login</title>
        <style>
            * { box-sizing: border-box; }
            body {
                margin: 0;
                min-height: 100vh;
                display: flex;
                align-items: center;
                justify-content: center;
                background: #0b1020;
                color: white;
                font-family: Arial, sans-serif;
            }
            .card {
                width: min(420px, 92%);
                padding: 35px;
                border-radius: 20px;
                background: #151c31;
                box-shadow: 0 20px 60px rgba(0,0,0,.35);
            }
            h1 { margin-top: 0; }
            p { color: #aeb8d0; }
            input {
                width: 100%;
                padding: 14px;
                margin: 18px 0 12px;
                border-radius: 10px;
                border: 1px solid #39445f;
                background: #0f1527;
                color: white;
                font-size: 16px;
            }
            button {
                width: 100%;
                padding: 14px;
                border: 0;
                border-radius: 10px;
                cursor: pointer;
                background: #ffffff;
                color: #111827;
                font-weight: bold;
                font-size: 15px;
            }
            #message {
                margin-top: 15px;
                min-height: 20px;
                color: #ff8d8d;
            }
        </style>
    </head>
    <body>
        <div class="card">
            <h1>RailSense Engineer Login</h1>
            <p>Enter the engineer access code to open the dashboard.</p>

            <input
                id="code"
                type="password"
                placeholder="Engineer access code"
                autocomplete="off"
            >

            <button onclick="login()">Enter Engineer Dashboard</button>

            <div id="message"></div>
        </div>

        <script>
            async function login() {
                const code = document.getElementById("code").value.trim();
                const message = document.getElementById("message");

                message.textContent = "";

                try {
                    const response = await fetch("/api/engineer/verify", {
                        method: "POST",
                        headers: {
                            "Content-Type": "application/json"
                        },
                        body: JSON.stringify({ code })
                    });

                    const data = await response.json();

                    if (data.ok) {
                        window.location.href = "/engineer";
                    } else {
                        message.textContent =
                            data.message || "Invalid engineer code.";
                    }
                } catch (error) {
                    message.textContent =
                        "Could not connect to the RailSense server.";
                }
            }

            document.getElementById("code").addEventListener("keydown", function(event) {
                if (event.key === "Enter") {
                    login();
                }
            });
        </script>
    </body>
    </html>
    """


# ============================================================
# ENGINEER DASHBOARD
# ============================================================

@app.route("/engineer")
@app.route("/engineer.html")
def engineer():
    return render_template("engineer.html")


# ============================================================
# HOME
# ============================================================

@app.route("/")
def home():
    return render_template("engineer (1).html")


# ============================================================
# ENGINEER-ONLY ACCESS GUARD
#
# The engineer pages and every engineer API (simulation, tickets,
# AI detection, evidence files, passenger-report review) require the
# session flag set by /api/engineer/verify. A signed-in passenger has
# no such flag, so none of these can be reached from a passenger login.
# Passenger routes (/passenger..., /api/passenger/..., /api/login,
# /api/signup) are NOT listed here and stay as they were.
# ============================================================

ENGINEER_ONLY_PAGES = ("/engineer", "/engineer.html")

ENGINEER_ONLY_API_PREFIXES = (
    "/api/stats",
    "/api/tickets",
    "/api/notifications",
    "/api/hotspots",
    "/api/system-health",
    "/api/simulation",
    "/api/ai/",
    "/api/image/",
    "/api/audio/",
    "/api/engineer/reports",
)


@app.before_request
def require_engineer():
    path = request.path

    is_page = path in ENGINEER_ONLY_PAGES
    is_api = path.startswith(ENGINEER_ONLY_API_PREFIXES)

    if not (is_page or is_api):
        return None

    if session.get("engineer"):
        return None

    if is_api:
        return jsonify({
            "ok": False,
            "error": "Engineer sign-in required."
        }), 401

    return redirect("/engineer-login")


# ============================================================
# ENGINEER VERIFY
# ============================================================

@app.route("/api/engineer/verify", methods=["POST"])
def verify_engineer():
    data = request.get_json(force=True) or {}
    code = str(data.get("code", "")).strip()

    if secrets.compare_digest(code, ENGINEER_SECRET):
        # Marks THIS browser as an engineer. Everything engineer-only is
        # checked against it by require_engineer() below.
        session["engineer"] = True
        return jsonify({
            "ok": True,
            "message": "Engineer access granted."
        })

    return jsonify({
        "ok": False,
        "message": "Invalid engineer code."
    }), 401

@app.route("/engineer (1)")
def landing():
    return render_template("engineer (1).html")


# ============================================================
# PASSENGER APP (templates/index.html)
# ============================================================

@app.route("/index")
@app.route("/index.html")
@app.route("/passenger")
def passenger_app():
    return render_template("index.html")
# ============================================================
# LOGIN
# ============================================================

@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json(force=True) or {}
    identifier = str(data.get("identifier", "")).strip()
    password = str(data.get("password", ""))

    errors = {}
    if not identifier:
        errors["identifier"] = "Phone / Email ID is required."
    if not password:
        errors["password"] = "Password is required."

    if errors:
        return jsonify({"ok": False, "errors": errors}), 400

    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM users WHERE lower(identifier)=lower(?)",
            (identifier,)
        ).fetchone()

        if row is None or not verify_password(password, row["password_hash"]):
            return jsonify({
                "ok": False,
                "errors": {
                    "identifier": "Invalid phone/email or password."
                }
            }), 401

        session["passenger"] = {
            "id": row["id"],
            "name": row["name"],
            "identifier": row["identifier"],
        }

        return jsonify({
            "ok": True,
            "name": row["name"],
            "redirect": "/passenger/dashboard",
            "message": f"Welcome back, {row['name']}."
        })
    finally:
        conn.close()


# ============================================================
# SIGNUP
# ============================================================

@app.route("/api/signup", methods=["POST"])
def signup():
    data = request.get_json(force=True) or {}

    identifier = str(data.get("identifier", "")).strip()
    password = str(data.get("password", ""))
    name = str(data.get("name", "")).strip()
    phone = normalize_phone(data.get("phone", ""))
    sms_alerts = 1 if data.get("smsAlerts", True) else 0

    errors = {}
    if not identifier:
        errors["identifier"] = "Phone / Email ID is required."
    elif "@" not in identifier and not identifier.isdigit():
        errors["identifier"] = "Enter a valid email address or 10-digit phone number."

    if not name:
        errors["name"] = "Full name is required."

    raw_phone = str(data.get("phone", "")).strip()
    if not raw_phone.isdigit() or len(raw_phone) != 10:
        errors["phone"] = "Phone number must be exactly 10 digits."

    if len(password) < 8:
        errors["password"] = "Password must be at least 8 characters."
    elif not any(c.isupper() for c in password):
        errors["password"] = "Password needs an uppercase letter."
    elif not any(c.islower() for c in password):
        errors["password"] = "Password needs a lowercase letter."
    elif not any(c.isdigit() for c in password):
        errors["password"] = "Password needs a number."
    elif not any(not c.isalnum() for c in password):
        errors["password"] = "Password needs a special character."

    if not data.get("agree"):
        errors["agree"] = "Please accept the User Agreement & Privacy Policy."

    if errors:
        return jsonify({"ok": False, "errors": errors}), 400

    conn = get_db()
    try:
        existing = conn.execute(
            "SELECT id FROM users WHERE lower(identifier)=lower(?)",
            (identifier,)
        ).fetchone()
        if existing:
            return jsonify({
                "ok": False,
                "errors": {
                    "identifier": "An account with this phone/email already exists."
                }
            }), 409

        conn.execute(
            """
            INSERT INTO users
                (identifier, password_hash, name, phone, sms_alerts, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                identifier,
                hash_password(password),
                name,
                phone,
                sms_alerts,
                datetime.now().isoformat()
            )
        )
        conn.commit()

        return jsonify({
            "ok": True,
            "message": "Account created and saved successfully. You can sign in anytime."
        })
    finally:
        conn.close()


# ============================================================
# REAL AUDIO PREDICTION API
# ============================================================

@app.route("/api/ai/audio/predict", methods=["POST"])
def audio_predict():
    if "audio" not in request.files:
        return jsonify({
            "ok": False,
            "error": "No audio file supplied."
        }), 400

    uploaded_file = request.files["audio"]

    if not uploaded_file.filename.lower().endswith(".wav"):
        return jsonify({
            "ok": False,
            "error": "Only WAV files are supported."
        }), 400

    temp_directory = os.path.join(BASE_DIR, "runtime_audio")
    os.makedirs(temp_directory, exist_ok=True)

    safe_name = os.path.basename(uploaded_file.filename)
    temp_path = os.path.join(temp_directory, safe_name)

    uploaded_file.save(temp_path)

    try:
        result = predict_audio_file(temp_path)

        return jsonify({
            "ok": True,
            "filename": safe_name,
            **result
        })

    except Exception as error:
        return jsonify({
            "ok": False,
            "error": str(error)
        }), 500

    finally:
        try:
            os.remove(temp_path)
        except Exception:
            pass


# ============================================================
# LOG-MEL SPECTROGRAM API
# ============================================================

@app.route("/api/ai/audio/spectrogram", methods=["POST"])
def audio_spectrogram():
    if "audio" not in request.files:
        return jsonify({
            "ok": False,
            "error": "No audio file supplied."
        }), 400

    uploaded_file = request.files["audio"]

    if not uploaded_file.filename.lower().endswith(".wav"):
        return jsonify({
            "ok": False,
            "error": "Only WAV files are supported."
        }), 400

    temp_directory = os.path.join(BASE_DIR, "runtime_audio")
    os.makedirs(temp_directory, exist_ok=True)

    safe_name = os.path.basename(uploaded_file.filename)
    temp_path = os.path.join(temp_directory, safe_name)

    uploaded_file.save(temp_path)

    try:
        image = create_log_mel_spectrogram(temp_path)

        return send_file(
            image,
            mimetype="image/png",
            download_name="log_mel_spectrogram.png"
        )

    except Exception as error:
        return jsonify({
            "ok": False,
            "error": str(error)
        }), 500

    finally:
        try:
            os.remove(temp_path)
        except Exception:
            pass


# ============================================================
# AUDIO WAVEFORM API
# ============================================================

@app.route("/api/ai/audio/waveform", methods=["POST"])
def audio_waveform():
    if "audio" not in request.files:
        return jsonify({
            "ok": False,
            "error": "No audio file supplied."
        }), 400

    uploaded_file = request.files["audio"]

    if not uploaded_file.filename.lower().endswith(".wav"):
        return jsonify({
            "ok": False,
            "error": "Only WAV files are supported."
        }), 400

    temp_directory = os.path.join(BASE_DIR, "runtime_audio")
    os.makedirs(temp_directory, exist_ok=True)

    safe_name = os.path.basename(uploaded_file.filename)
    temp_path = os.path.join(temp_directory, safe_name)

    uploaded_file.save(temp_path)

    try:
        image = create_waveform_image(temp_path)

        return send_file(
            image,
            mimetype="image/png",
            download_name="waveform.png"
        )

    except Exception as error:
        return jsonify({
            "ok": False,
            "error": str(error)
        }), 500

    finally:
        try:
            os.remove(temp_path)
        except Exception:
            pass


# ============================================================
# MFCC VISUALIZATION API
# ============================================================

@app.route("/api/ai/audio/mfcc", methods=["POST"])
def audio_mfcc():
    if "audio" not in request.files:
        return jsonify({
            "ok": False,
            "error": "No audio file supplied."
        }), 400

    uploaded_file = request.files["audio"]

    if not uploaded_file.filename.lower().endswith(".wav"):
        return jsonify({
            "ok": False,
            "error": "Only WAV files are supported."
        }), 400

    temp_directory = os.path.join(BASE_DIR, "runtime_audio")
    os.makedirs(temp_directory, exist_ok=True)

    safe_name = os.path.basename(uploaded_file.filename)
    temp_path = os.path.join(temp_directory, safe_name)

    uploaded_file.save(temp_path)

    try:
        image = create_mfcc_image(temp_path)

        return send_file(
            image,
            mimetype="image/png",
            download_name="mfcc.png"
        )

    except Exception as error:
        return jsonify({
            "ok": False,
            "error": str(error)
        }), 500

    finally:
        try:
            os.remove(temp_path)
        except Exception:
            pass


# ============================================================
# IMAGE EVIDENCE
# ============================================================

@app.route("/api/image/evidence/<path:filename>")
def image_evidence(filename):
    filename = os.path.basename(filename)

    for root, _, files in os.walk(IMAGE_TESTING_PATH):
        if filename in files:
            return send_file(
                os.path.join(root, filename),
                mimetype="image/jpeg"
            )

    return jsonify({"error": "Image evidence not found."}), 404


@app.route("/api/image/gradcam/<path:filename>")
def image_gradcam(filename):
    filename = os.path.basename(filename)
    path = os.path.join(IMAGE_GRADCAM_DIR, filename)

    if not os.path.isfile(path):
        return jsonify({"error": "Grad-CAM image not found."}), 404

    return send_file(path, mimetype="image/png")


# ============================================================
# AUDIO EVIDENCE
# ============================================================

@app.route("/api/audio/evidence/<path:filename>")
def audio_evidence(filename):
    filename = os.path.basename(filename)

    for root, _, files in os.walk(AUDIO_TESTING_PATH):
        if filename in files:
            return send_file(
                os.path.join(root, filename),
                mimetype="audio/wav"
            )

    return jsonify({
        "error": "Audio evidence not found."
    }), 404


# ============================================================
# STATS
# ============================================================

@app.route("/api/stats")
def stats():
    with DATABASE_LOCK:
        total = len(TICKETS)
        active = len([
            x for x in TICKETS
            if x["status"] == "Active"
        ])
        resolved = len([
            x for x in TICKETS
            if x["status"] == "Resolved"
        ])

    return jsonify({
        "total_detections": total,
        "active_tickets": active,
        "resolved_tickets": resolved,
        "incorrect_predictions": 0
    })


# ============================================================
# TICKETS
# ============================================================

@app.route("/api/tickets")
def tickets():
    train_number = request.args.get(
        "train_number", ""
    ).strip()

    with DATABASE_LOCK:
        result = [
            dict(ticket)
            for ticket in TICKETS
            if ticket["status"] == "Active"
        ]

    if train_number:
        result = [
            ticket for ticket in result
            if ticket["train_number"] == train_number
        ]

    result.reverse()
    return jsonify(result)


# ============================================================
# SINGLE TICKET
# ============================================================

@app.route("/api/tickets/<int:ticket_id>")
def get_ticket(ticket_id):
    with DATABASE_LOCK:
        for ticket in TICKETS:
            if ticket["id"] == ticket_id:
                return jsonify(ticket)

    return jsonify({
        "error": "Ticket not found."
    }), 404


# ============================================================
# RESOLVE TICKET
# ============================================================

@app.route("/api/tickets/<int:ticket_id>/resolve", methods=["POST"])
def resolve_ticket(ticket_id):
    with DATABASE_LOCK:
        for ticket in TICKETS:
            if ticket["id"] == ticket_id:
                ticket["status"] = "Resolved"
                ticket["resolved_at"] = datetime.now().isoformat()

                return jsonify({
                    "ok": True,
                    "message": "Ticket resolved."
                })

    return jsonify({
        "ok": False,
        "message": "Ticket not found."
    }), 404


# ============================================================
# INFORM PASSENGERS
# ============================================================

@app.route(
    "/api/tickets/<int:ticket_id>/inform-passengers",
    methods=["POST"]
)
def inform_passengers(ticket_id):
    data = request.get_json(force=True) or {}
    maintenance_date = str(data.get("maintenance_date", "")).strip()

    if not maintenance_date:
        return jsonify({
            "ok": False,
            "message": "Please select a scheduled maintenance date."
        }), 400

    try:
        maintenance_date_obj = datetime.strptime(
            maintenance_date, "%Y-%m-%d"
        )
        formatted_date = maintenance_date_obj.strftime("%d %B %Y")
    except ValueError:
        return jsonify({
            "ok": False,
            "message": "Invalid maintenance date."
        }), 400

    with DATABASE_LOCK:
        ticket = next(
            (x for x in TICKETS if x["id"] == ticket_id),
            None
        )

    if ticket is None:
        return jsonify({
            "ok": False,
            "message": "Ticket not found."
        }), 404

    if ticket["severity"] != "Critical":
        return jsonify({
            "ok": False,
            "message": "Passenger notification is available only for Critical defects."
        }), 400

    message = (
        f"RailSense safety update: Train {ticket['train_number']} "
        f"({ticket['train_name']}) has a critical {ticket['defect_type']} "
        f"near {ticket['location_name']}. Scheduled maintenance date: "
        f"{formatted_date}. Please follow railway instructions and check "
        f"your journey status before travelling."
    )

    conn = get_db()
    sent = 0
    failed = []

    try:
        rows = conn.execute(
            """
            SELECT id, name, phone, sms_alerts
            FROM users
            WHERE sms_alerts = 1
              AND phone IS NOT NULL
              AND phone != ''
            """
        ).fetchall()
    finally:
        conn.close()

    print("=" * 60)
    print("PASSENGER SMS DEBUG")
    print("=" * 60)
    print("Database:", DATABASE_PATH)
    print("Passenger contacts found:", len(rows))
    print("Twilio Account SID loaded:", bool(TWILIO_ACCOUNT_SID))
    print("Twilio Auth Token loaded:", bool(TWILIO_AUTH_TOKEN))
    print("Twilio From Phone:", TWILIO_FROM_PHONE)
    print("=" * 60)

    for row in rows:
        print(
            f"Passenger: {row['name']} | "
            f"Phone: {row['phone']} | "
            f"SMS alerts: {row['sms_alerts']}"
        )

    for row in rows:
        try:
            send_sms(row["phone"], message)
            sent += 1

        except Exception as error:
            print("=" * 60)
            print("TWILIO SMS ERROR")
            print("=" * 60)
            print("Passenger:", row["name"])
            print("Phone:", row["phone"])
            print("Error type:", type(error).__name__)
            print("Error:", str(error))
            print("=" * 60)

            failed.append({
                "phone": row["phone"],
                "error": str(error)
            })

    with DATABASE_LOCK:
        ticket["maintenance_date"] = maintenance_date
        # The in-app passenger alert is published as soon as the engineer
        # confirms. It must not depend on Twilio: without SMS credentials
        # (demo / trial) passengers would otherwise never see the alert.
        ticket["passengers_informed"] = True
        ticket["sms_sent_count"] = sent
        notification = {
            "id": len(NOTIFICATIONS) + 1,
            "ticket_id": ticket["id"],
            "type": "passengers",
            "target": "Passengers",
            "message": message,
            "status": "SENT" if sent else "FAILED",
            "sent_count": sent,
            "failed_count": len(failed),
            "created_at": datetime.now().isoformat(),
        }
        NOTIFICATIONS.append(notification)

    if not sent:
        return jsonify({
            "ok": False,
            "message": (
                "No passenger SMS was sent (check the database contacts "
                "and Twilio configuration). The in-app passenger alert "
                "has still been published."
            ),
            "notification": notification,
            "failures": failed
        }), 502

    return jsonify({
        "ok": True,
        "simulated": False,
        "trial_mode": TWILIO_TRIAL_MODE,
        "message": (
            f"SMS sent to {sent} passenger contact(s)."
            + (
                " (Twilio trial account: passengers received Twilio's "
                "fixed template text, not this custom message, until "
                "you upgrade.)"
                if TWILIO_TRIAL_MODE else ""
            )
        ),
        "notification": notification,
        "failures": failed
    })


# ============================================================
# INFORM ROUTING OFFICER
# ============================================================

@app.route(
    "/api/tickets/<int:ticket_id>/inform-routing-officer",
    methods=["POST"]
)
def inform_routing_officer(ticket_id):
    with DATABASE_LOCK:
        ticket = next(
            (x for x in TICKETS if x["id"] == ticket_id),
            None
        )

    if ticket is None:
        return jsonify({
            "ok": False,
            "message": "Ticket not found."
        }), 404

    if ticket["severity"] != "Critical":
        return jsonify({
            "ok": False,
            "message": "Routing notification is available only for Critical defects."
        }), 400

    routing_numbers = [
        item.strip()
        for item in ROUTING_OFFICER_PHONE.split(",")
        if item.strip()
    ]

    if not routing_numbers:
        return jsonify({
            "ok": False,
            "message": "ROUTING_OFFICER_PHONE is not configured."
        }), 500

    message = (
        f"RailSense OPERATIONS ALERT: CRITICAL {ticket['defect_type']} "
        f"detected on Train {ticket['train_number']} "
        f"({ticket['train_name']}) near {ticket['location_name']}. "
        f"Location: {ticket['latitude']:.6f}, {ticket['longitude']:.6f}. "
        f"Routing intervention required."
    )

    sent_sms = 0
    sent_whatsapp = 0
    failures = []

    for phone in routing_numbers:
        try:
            send_sms(
                phone, message,
                template_keyword=TWILIO_TRIAL_TEMPLATE_ROUTING
            )
            sent_sms += 1
        except Exception as error:
            failures.append({
                "channel": "SMS",
                "phone": phone,
                "error": str(error)
            })

        if TWILIO_WHATSAPP_FROM:
            try:
                send_whatsapp(phone, message)
                sent_whatsapp += 1
            except Exception as error:
                failures.append({
                    "channel": "WhatsApp",
                    "phone": phone,
                    "error": str(error)
                })

    sent_any = sent_sms > 0 or sent_whatsapp > 0

    with DATABASE_LOCK:
        ticket["routing_officer_informed"] = sent_any
        notification = {
            "id": len(NOTIFICATIONS) + 1,
            "ticket_id": ticket["id"],
            "type": "routing",
            "target": "Routing Officer",
            "message": message,
            "status": "SENT" if sent_any else "FAILED",
            "sent_sms": sent_sms,
            "sent_whatsapp": sent_whatsapp,
            "created_at": datetime.now().isoformat(),
        }
        NOTIFICATIONS.append(notification)

    if not sent_any:
        return jsonify({
            "ok": False,
            "message": "Routing officer notification failed.",
            "notification": notification,
            "failures": failures
        }), 502

    return jsonify({
        "ok": True,
        "simulated": False,
        "trial_mode": TWILIO_TRIAL_MODE,
        "message": (
            f"Operations control notified by SMS ({sent_sms})"
            + (f" and WhatsApp ({sent_whatsapp})." if sent_whatsapp else ".")
            + (
                " (Twilio trial account: the fixed trial template text "
                "was delivered, not this custom message.)"
                if TWILIO_TRIAL_MODE else ""
            )
        ),
        "notification": notification,
        "failures": failures
    })


# ============================================================
# NOTIFICATIONS
# ============================================================

@app.route("/api/notifications")
def notifications():
    with DATABASE_LOCK:
        result = list(NOTIFICATIONS)

    result.reverse()
    return jsonify(result)


# ============================================================
# HOTSPOTS
# ============================================================

@app.route("/api/hotspots")
def hotspots():
    counts = {}
    recent_counts = {}

    with DATABASE_LOCK:
        tickets_copy = list(TICKETS)

    for ticket in tickets_copy:
        location = ticket["location_name"]

        counts[location] = counts.get(location, 0) + 1

        try:
            created = datetime.fromisoformat(ticket["created_at"])
            age = (
                datetime.now() - created
            ).total_seconds()

            if age <= 900:
                recent_counts[location] = (
                    recent_counts.get(location, 0) + 1
                )
        except Exception:
            pass

    result = []

    for location, count in counts.items():
        result.append({
            "location_name": location,
            "defect_count": count,
            "recent_count": recent_counts.get(location, 0)
        })

    result.sort(
        key=lambda x: x["defect_count"],
        reverse=True
    )

    return jsonify(result)


# ============================================================
# SYSTEM HEALTH
# ============================================================

@app.route("/api/system-health")
def system_health():
    audio_status = (
        "Operational"
        if (
            svm_model is not None
            and scaler is not None
            and len(get_audio_files()) > 0
        )
        else "Attention required"
    )

    return jsonify({
        "audio_pipeline": audio_status,
        "image_pipeline": (
            "Operational"
            if image_model is not None and len(get_image_files()) > 0
            else "Attention required"
        ),
        "detection_engine": "Operational",
        "database": "Operational",
        "live_inspection": (
            "Operational"
            if SIMULATION_RUNNING
            else "Standby"
        )
    })


# ============================================================
# SIMULATION START
# ============================================================

@app.route("/api/simulation/start", methods=["POST"])
def start_simulation():
    global SIMULATION_RUNNING
    global SIMULATION_THREAD

    with SIMULATION_LOCK:
        if SIMULATION_RUNNING:
            return jsonify({
                "ok": True,
                "running": True,
                "message": "Simulation already running."
            })

        SIMULATION_RUNNING = True

        SIMULATION_THREAD = threading.Thread(
            target=simulation_worker,
            daemon=True
        )

        SIMULATION_THREAD.start()

    print("RailSense live inspection started.")

    return jsonify({
        "ok": True,
        "running": True,
        "message": (
            "Live AI inspection started across "
            "the 8 RailSense Express trains."
        )
    })


# ============================================================
# SIMULATION STOP
# ============================================================

@app.route("/api/simulation/stop", methods=["POST"])
def stop_simulation():
    global SIMULATION_RUNNING

    with SIMULATION_LOCK:
        SIMULATION_RUNNING = False

    print("RailSense live inspection stopped.")

    return jsonify({
        "ok": True,
        "running": False,
        "message": "Live inspection stopped."
    })


# ============================================================
# SIMULATION STATUS
# ============================================================

@app.route("/api/simulation/status")
def simulation_status():
    with SIMULATION_LOCK:
        running = SIMULATION_RUNNING

    return jsonify({
        "running": running,
        "trains": get_all_trains()
    })


# ============================================================
# MANUAL SIMULATION TICK
# ============================================================

@app.route("/api/simulation/tick", methods=["POST"])
def manual_tick():
    try:
        simulation_tick()

        return jsonify({
            "ok": True,
            "trains": get_all_trains()
        })

    except Exception as error:
        return jsonify({
            "ok": False,
            "error": str(error)
        }), 500


# ============================================================
# HEALTH CHECK
# ============================================================

@app.route("/api/health")
def health():
    return jsonify({
        "ok": True,
        "service": "RailSense Flask backend"
    })


# ============================================================
# PASSENGER DASHBOARD
#
# Passenger-facing view of the SAME train/ticket/notification data
# the Engineer dashboard uses (TRAIN_STATE, TICKETS, NOTIFICATIONS).
# Nothing passenger-only is stored. Technical AI fields (audio/image
# input, defect class, confidence, probabilities, Grad-CAM, evidence
# files) are deliberately never copied into the passenger response.
#
# A passenger only sees an alert once the engineer has chosen
# "Inform passengers" on a Critical ticket (passengers_informed).
# ============================================================

# Used until the passenger picks a journey in the future My Journey
# feature (stored in the passenger_journeys table).
DEFAULT_PASSENGER_TRAIN = "12951"

PASSENGER_PLACEHOLDER_PAGES = {
    "report": {
        "title": "Report Issue",
        "nav": "report",
        "text": "Report something unusual you notice during your journey. Reporting will open here.",
    },
    "chat": {
        "title": "Passenger Chat",
        "nav": "chat",
        "text": "Passengers travelling on your train will be able to talk here.",
    },
    "reports": {
        "title": "My Reports",
        "nav": "reports",
        "text": "Issues you have reported and their progress will be tracked here.",
    },
    "emergency": {
        "title": "Emergency Help",
        "nav": "emergency",
        "text": "In-app emergency assistance is not available yet.",
        "emergency": True,
    },
}


def current_passenger():
    return session.get("passenger")


def passenger_context(passenger):
    parts = [p for p in str(passenger.get("name", "")).split() if p]
    initials = "".join(p[0] for p in parts[:2]).upper() or "P"
    return {
        "name": passenger.get("name", "Passenger"),
        "initials": initials,
    }


def passenger_place(location_name):
    """'Surat → Vadodara' -> (['Surat','Vadodara'], 'between Surat and Vadodara')."""
    location_name = str(location_name or "").strip()

    if "→" in location_name:
        start, end = [p.strip() for p in location_name.split("→", 1)]
        return [start, end], f"between {start} and {end}"

    return [location_name], f"near {location_name}"


def passenger_train_category(train_name):
    return "Superfast Express" if "Superfast" in train_name else "Express"


def passenger_selected_train(user_id):
    try:
        conn = get_db()
        try:
            row = conn.execute(
                "SELECT train_number FROM passenger_journeys WHERE user_id = ?",
                (user_id,)
            ).fetchone()
        finally:
            conn.close()

        if row and row["train_number"] in TRAIN_STATE:
            return row["train_number"], False
    except Exception as error:
        print("Passenger journey lookup error:", error)

    return DEFAULT_PASSENGER_TRAIN, True


def format_maintenance_date(value):
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").strftime("%d %B %Y")
    except Exception:
        return ""


def build_passenger_dashboard(train_number):
    with SIMULATION_LOCK:
        train = dict(TRAIN_STATE[train_number])

    with DATABASE_LOCK:
        train_tickets = [
            dict(t) for t in TICKETS
            if t["train_number"] == train_number
        ]
        ticket_ids = {t["id"] for t in train_tickets}
        sent_notifications = [
            dict(n) for n in NOTIFICATIONS
            if n.get("type") == "passengers"
            and n.get("ticket_id") in ticket_ids
            and n.get("status") == "SENT"
        ]

    informed = [t for t in train_tickets if t.get("passengers_informed")]
    active = [t for t in informed if t["status"] == "Active"]
    resolved = [t for t in informed if t["status"] == "Resolved"]

    alert_ticket = max(active, key=lambda t: t["created_at"]) if active else None

    # ---------------- route preview ----------------
    route = train["route"]
    names = [stop["name"] for stop in route]
    position = train["route_index"] + train["position_fraction"]

    affected = set()
    segment_alert = False
    if alert_ticket:
        parts, _ = passenger_place(alert_ticket["location_name"])
        affected = {i for i, n in enumerate(names) if n in parts}
        segment_alert = len(parts) == 2

    stations = []
    for index, name in enumerate(names):
        if abs(position - index) < 1e-9:
            progress_state = "current"
        elif position > index:
            progress_state = "passed"
        else:
            progress_state = "upcoming"

        stations.append({
            "name": name,
            "state": progress_state,
            "status": "caution" if index in affected else "ok",
        })

    segments = []
    for index in range(len(names) - 1):
        segments.append({
            "caution": segment_alert and index in affected and (index + 1) in affected,
            "has_train": index < position < index + 1,
        })

    at_station = "→" not in train["location_name"]
    if at_station:
        current_label = train["location_name"]
    else:
        parts, _ = passenger_place(train["location_name"])
        current_label = f"Between {parts[0]} and {parts[1]}"

    progress = position / max(len(names) - 1, 1)

    # ---------------- alert ----------------
    if alert_ticket:
        _, place = passenger_place(alert_ticket["location_name"])
        date_text = format_maintenance_date(alert_ticket.get("maintenance_date"))
        message = f"Maintenance activity has been scheduled {place}"
        message += f" on {date_text}." if date_text else "."

        alert = {
            "active": True,
            "message": message,
            "impact": "Your journey may be affected.",
            "instructions": [
                "Follow instructions from railway staff.",
                "Check your journey status before travelling.",
            ],
            "issued_at": alert_ticket["created_at"],
        }
        status = {
            "tone": "caution",
            "label": "Maintenance Advisory",
            "detail": f"Maintenance activity {place}.",
        }
    else:
        alert = {"active": False}
        status = {
            "tone": "ok",
            "label": "Operating Normally",
            "detail": "No active alerts on your route.",
        }

    # ---------------- recent updates ----------------
    updates = []
    ticket_by_id = {t["id"]: t for t in train_tickets}

    for note in sent_notifications:
        ticket = ticket_by_id.get(note["ticket_id"])
        if not ticket:
            continue
        _, place = passenger_place(ticket["location_name"])
        updates.append({
            "time": note["created_at"],
            "text": f"Maintenance activity {place}.",
            "tone": "caution",
        })

    for ticket in resolved:
        _, place = passenger_place(ticket["location_name"])
        updates.append({
            "time": ticket.get("resolved_at") or ticket["created_at"],
            "text": f"Maintenance {place} is complete. Normal operation resumed.",
            "tone": "ok",
        })

    updates.append({
        "time": train.get("last_inspection") or datetime.now().isoformat(),
        "text": (
            "Journey operating normally."
            if not alert_ticket
            else "Your route is being monitored. Maintenance advisory in effect."
        ),
        "tone": "ok" if not alert_ticket else "info",
    })

    updates.sort(key=lambda u: u["time"], reverse=True)

    return {
        "ok": True,
        "source": "backend",
        "journey": {
            "train_number": train["train_number"],
            "train_name": train["name"],
            "category": passenger_train_category(train["name"]),
            "from_station": train["from_station"],
            "to_station": train["to_station"],
            "current_label": current_label,
            "destination": train["to_station"],
            "progress": round(progress, 4),
            "status": status,
        },
        "route": {"stations": stations, "segments": segments},
        "alert": alert,
        "updates": updates[:6],
        "generated_at": datetime.now().isoformat(),
    }


@app.route("/passenger/dashboard")
def passenger_dashboard():
    passenger = current_passenger()
    if not passenger:
        return redirect("/passenger")

    return render_template(
        "passenger_dashboard.html",
        passenger=passenger_context(passenger),
        active="dashboard",
    )


@app.route("/passenger/<page>")
def passenger_placeholder(page):
    passenger = current_passenger()
    if not passenger:
        return redirect("/passenger")

    info = PASSENGER_PLACEHOLDER_PAGES.get(page)
    if info is None:
        return jsonify({"ok": False, "error": "Page not found."}), 404

    return render_template(
        "passenger_placeholder.html",
        passenger=passenger_context(passenger),
        active=info["nav"],
        page=info,
    )


# ============================================================
# PASSENGER PROFILE
# ============================================================

def profile_password_error(password):
    """Same rules as signup."""
    if len(password) < 8:
        return "Password must be at least 8 characters."
    if not any(c.isupper() for c in password):
        return "Password needs an uppercase letter."
    if not any(c.islower() for c in password):
        return "Password needs a lowercase letter."
    if not any(c.isdigit() for c in password):
        return "Password needs a number."
    if not any(not c.isalnum() for c in password):
        return "Password needs a special character."
    return None


@app.route("/passenger/profile")
def passenger_profile_page():
    passenger = current_passenger()
    if not passenger:
        return redirect("/passenger")

    return render_template(
        "passenger_profile.html",
        passenger=passenger_context(passenger),
        active="profile",
    )


@app.route("/api/passenger/profile", methods=["GET", "POST"])
def passenger_profile_api():
    passenger = current_passenger()
    if not passenger:
        return jsonify({"ok": False, "error": "Please sign in."}), 401

    user_id = passenger["id"]

    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        updates, values, errors = [], [], {}

        if "name" in data:
            name = " ".join(str(data.get("name", "")).split())
            if not name:
                errors["name"] = "Full name is required."
            elif len(name) > 60:
                errors["name"] = "Name can be up to 60 characters."
            else:
                updates.append("name = ?")
                values.append(name)

        if "phone" in data:
            raw_phone = str(data.get("phone", "")).strip()
            if not raw_phone.isdigit() or len(raw_phone) != 10:
                errors["phone"] = "Phone number must be exactly 10 digits."
            else:
                updates.append("phone = ?")
                values.append(normalize_phone(raw_phone))

        if "sms_alerts" in data:
            updates.append("sms_alerts = ?")
            values.append(1 if data.get("sms_alerts") else 0)

        if errors:
            return jsonify({"ok": False, "errors": errors}), 400
        if not updates:
            return jsonify({"ok": False, "error": "Nothing to update."}), 400

        conn = get_db()
        try:
            conn.execute(
                "UPDATE users SET " + ", ".join(updates) + " WHERE id = ?",
                (*values, user_id)
            )
            conn.commit()
            row = conn.execute("SELECT name FROM users WHERE id = ?", (user_id,)).fetchone()
        finally:
            conn.close()

        # Keep the signed-in session in step with the saved name.
        session["passenger"] = {**passenger, "name": row["name"]}
        return jsonify({"ok": True, "name": row["name"]})

    # ---- GET ----
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT identifier, name, phone, sms_alerts, created_at FROM users WHERE id = ?",
            (user_id,)
        ).fetchone()
        reports_count = conn.execute(
            "SELECT COUNT(*) FROM passenger_reports WHERE user_id = ?", (user_id,)
        ).fetchone()[0]
    finally:
        conn.close()

    if row is None:
        session.pop("passenger", None)
        return jsonify({"ok": False, "error": "Please sign in."}), 401

    member_since = ""
    created = journey_iso(row["created_at"])
    if created:
        member_since = created.strftime("%d %b %Y")

    journey = None
    saved = journey_saved(user_id)
    if saved:
        train_number, source, destination = saved
        with SIMULATION_LOCK:
            train_name = TRAIN_STATE[train_number]["name"]
        journey = {
            "train_number": train_number,
            "train_name": train_name,
            "route": f"{source} → {destination}" if source and destination else "",
        }

    return jsonify({
        "ok": True,
        "name": row["name"],
        "identifier": row["identifier"],
        "phone": str(row["phone"] or "")[-10:],
        "sms_alerts": bool(row["sms_alerts"]),
        "member_since": member_since,
        "journey": journey,
        "reports_count": reports_count,
    })


@app.route("/api/passenger/profile/password", methods=["POST"])
def passenger_profile_password():
    passenger = current_passenger()
    if not passenger:
        return jsonify({"ok": False, "error": "Please sign in."}), 401

    data = request.get_json(silent=True) or {}
    current = str(data.get("current", ""))
    new_password = str(data.get("password", ""))

    problem = profile_password_error(new_password)
    if problem:
        return jsonify({"ok": False, "error": problem}), 400

    conn = get_db()
    try:
        row = conn.execute(
            "SELECT password_hash FROM users WHERE id = ?", (passenger["id"],)
        ).fetchone()
        if row is None or not verify_password(current, row["password_hash"]):
            return jsonify({"ok": False, "error": "Current password is incorrect."}), 400

        conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (hash_password(new_password), passenger["id"])
        )
        conn.commit()
    finally:
        conn.close()

    return jsonify({"ok": True})


@app.route("/api/passenger/dashboard")
def passenger_dashboard_data():
    passenger = current_passenger()
    if not passenger:
        return jsonify({"ok": False, "error": "Please sign in."}), 401

    # ?train=NNNNN lets a train be previewed before My Journey exists.
    requested = str(request.args.get("train", "")).strip()

    if requested in TRAIN_STATE:
        train_number = requested
    else:
        train_number, _ = passenger_selected_train(passenger["id"])

    data = build_passenger_dashboard(train_number)
    data["passenger"] = passenger_context(passenger)
    data["unread_alerts"] = alerts_unread_count(passenger["id"])
    return jsonify(data)


@app.route("/api/passenger/logout", methods=["POST"])
def passenger_logout():
    session.pop("passenger", None)
    return jsonify({"ok": True, "redirect": "/passenger"})


# ============================================================
# MY JOURNEY (passenger route status)
#
# Reads the SAME TRAIN_STATE / TICKETS / NOTIFICATIONS that the
# Engineer dashboard writes. Nothing passenger-only is stored
# except which train + stations the passenger chose.
#
# Ticket -> passenger colour:
#   Critical + engineer pressed "Inform passengers" + Active -> red
#   Abnormal (medium) + Active                              -> yellow
#   Critical + informed + Resolved (recent)                 -> green "Maintenance Completed"
#   anything else                                           -> green
# Unresolved Critical tickets the engineer has NOT yet released
# to passengers stay hidden (same rule the dashboard already uses).
#
# Tickets are matched by train_number, so an alert on one train
# never appears on another. Within a train, only tickets located
# inside the passenger's chosen Source -> Destination stretch count.
#
# Raw AI fields (input type, defect class, confidence, evidence,
# Grad-CAM, ticket ids) are never copied into the response.
# ============================================================

JOURNEY_SHOW_ABNORMAL = True            # False = medium issues stay invisible to passengers
JOURNEY_COMPLETED_VISIBLE_MINUTES = 120  # how long "Maintenance Completed" stays visible
JOURNEY_LEVEL_RANK = {"ok": 0, "completed": 1, "warning": 2, "critical": 3}


def journey_catalog():
    with SIMULATION_LOCK:
        trains = [dict(t) for t in TRAIN_STATE.values()]

    return [
        {
            "train_number": t["train_number"],
            "name": t["name"],
            "category": passenger_train_category(t["name"]),
            "stations": [stop["name"] for stop in t["route"]],
        }
        for t in trains
    ]


def journey_saved(user_id):
    try:
        conn = get_db()
        try:
            row = conn.execute(
                "SELECT train_number, source_station, destination_station "
                "FROM passenger_journeys WHERE user_id = ?",
                (user_id,)
            ).fetchone()
        finally:
            conn.close()

        if row and row["train_number"] in TRAIN_STATE:
            return row["train_number"], row["source_station"], row["destination_station"]
    except Exception as error:
        print("Passenger journey lookup error:", error)

    return None


def journey_save(user_id, train_number, source, destination):
    conn = get_db()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO passenger_journeys "
            "(user_id, train_number, selected_at, source_station, destination_station) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, train_number, datetime.now().isoformat(), source, destination)
        )
        conn.commit()
    finally:
        conn.close()


def journey_iso(value):
    try:
        return datetime.fromisoformat(str(value))
    except Exception:
        return None


def journey_ticket_target(ticket, names):
    """('stn', i) for a station, ('seg', i) for the stretch names[i] -> names[i+1]."""
    parts, _ = passenger_place(ticket.get("location_name"))

    if len(parts) == 2:
        if parts[0] in names and parts[1] in names:
            a, b = names.index(parts[0]), names.index(parts[1])
            if b == a + 1:
                return "seg", a
        return None

    if parts[0] in names:
        return "stn", names.index(parts[0])

    return None


def journey_ticket_level(ticket, now):
    severity = ticket.get("severity")
    status = ticket.get("status")

    if severity == "Critical":
        if not ticket.get("passengers_informed"):
            return None
        if status == "Active":
            return "critical"
        if status == "Resolved":
            resolved_at = journey_iso(ticket.get("resolved_at"))
            if resolved_at and (now - resolved_at).total_seconds() <= JOURNEY_COMPLETED_VISIBLE_MINUTES * 60:
                return "completed"
        return None

    if severity == "Abnormal" and JOURNEY_SHOW_ABNORMAL and status == "Active":
        return "warning"

    return None


def journey_section(kind, index, names, level, ticket, updated_at):
    if kind == "stn":
        label = names[index]
        place = f"near {names[index]}"
        section_id = f"s{index}"
    else:
        label = f"{names[index]} \u2013 {names[index + 1]}"
        place = f"between {names[index]} and {names[index + 1]}"
        section_id = f"g{index}"

    section = {
        "id": section_id,
        "kind": kind,
        "title": f"{label} Section",
        "status": "ok",
        "completed": False,
        "headline": "Normal",
        "message": "No active maintenance alert currently affects this section.",
        "maintenance": None,
        "maintenance_note": None,
        "impact": "No impact on your journey.",
        "instructions": [],
        "updated_at": updated_at,
        "place": place,
    }

    if level == "critical":
        date_text = format_maintenance_date(ticket.get("maintenance_date")) if ticket else ""
        section.update({
            "status": "critical",
            "headline": "Maintenance Activity",
            "message": f"A track-related issue has been identified {place}.",
            "maintenance": "Scheduled",
            "maintenance_note": (
                "RailSense has notified the maintenance system for this section."
                + (f" Planned date: {date_text}." if date_text else "")
            ),
            "impact": "Possible delay or speed restriction.",
            "instructions": [
                "Follow instructions from railway staff.",
                "Check your journey status before travelling.",
            ],
        })
    elif level == "warning":
        section.update({
            "status": "warning",
            "headline": "Under Observation",
            "message": f"A minor track condition {place} is being monitored.",
            "maintenance": "Under observation",
            "maintenance_note": "RailSense is keeping this section under watch.",
            "impact": "Not expected to affect your journey.",
            "instructions": ["No action needed. Follow railway staff instructions if any are given."],
        })
    elif level == "completed":
        section.update({
            "completed": True,
            "headline": "Maintenance Completed",
            "maintenance": "Completed",
        })

    return section


def build_passenger_journey(train_number, source, destination):
    with SIMULATION_LOCK:
        train = dict(TRAIN_STATE[train_number])

    names = [stop["name"] for stop in train["route"]]
    last = len(names) - 1

    si = names.index(source) if source in names else 0
    di = names.index(destination) if destination in names else last
    if si >= di:
        si, di = 0, last

    now = datetime.now()
    position = train["route_index"] + train["position_fraction"]
    nearest = round(position)
    at_station = abs(position - nearest) < 1e-9

    # ---------------- tickets for THIS train only ----------------
    with DATABASE_LOCK:
        train_tickets = [dict(t) for t in TICKETS if t["train_number"] == train_number]
        sent_at = {}
        for n in NOTIFICATIONS:
            if n.get("type") == "passengers" and n.get("status") == "SENT":
                sent_at[n.get("ticket_id")] = max(
                    sent_at.get(n.get("ticket_id"), ""), n.get("created_at", "")
                )

    best = {}  # section key -> (rank, time, level, ticket)
    for ticket in train_tickets:
        level = journey_ticket_level(ticket, now)
        if level is None:
            continue

        target = journey_ticket_target(ticket, names)
        if target is None:
            continue

        kind, index = target
        inside = (si <= index <= di) if kind == "stn" else (si <= index and index + 1 <= di)
        if not inside:
            continue

        if level == "critical":
            stamp = sent_at.get(ticket["id"]) or ticket["created_at"]
        elif level == "completed":
            stamp = ticket.get("resolved_at") or ticket["created_at"]
        else:
            stamp = ticket["created_at"]

        key = (kind, index)
        candidate = (JOURNEY_LEVEL_RANK[level], stamp, level, ticket)
        if key not in best or candidate[:2] > best[key][:2]:
            best[key] = candidate

    fallback_time = train.get("last_inspection") or now.isoformat()

    def make(kind, index):
        entry = best.get((kind, index))
        if entry:
            return journey_section(kind, index, names, entry[2], entry[3], entry[1])
        return journey_section(kind, index, names, "ok", None, fallback_time)

    sections = {}
    stations = []
    segments = []

    for index in range(si, di + 1):
        sec = make("stn", index)
        sections[sec["id"]] = sec

        if abs(position - index) < 1e-9:
            state = "current"
        elif position > index:
            state = "passed"
        else:
            state = "upcoming"

        stations.append({
            "id": sec["id"], "name": names[index], "state": state,
            "status": sec["status"], "completed": sec["completed"],
        })

        if index < di:
            seg = make("seg", index)
            sections[seg["id"]] = seg
            segments.append({
                "id": seg["id"],
                "status": seg["status"],
                "completed": seg["completed"],
                "has_train": index < position < index + 1,
                "passed": position >= index + 1,
            })

    # ---------------- position ----------------
    if at_station:
        current_label = names[nearest]
        next_index = nearest + 1
    else:
        lower = int(position)
        current_label = f"Between {names[lower]} and {names[lower + 1]}"
        next_index = lower + 1

    next_station = names[next_index] if (next_index <= di and next_index <= last) else None

    if position < si - 1e-9:
        state = "approaching"
        note = f"The train has not reached {names[si]} yet."
        next_station = names[si]
    elif position >= di - 1e-9:
        state = "arrived"
        note = f"The train has reached {names[di]}."
        next_station = None
    else:
        state = "in_progress"
        note = ""

    progress = max(0.0, min(1.0, (position - si) / max(di - si, 1)))

    # ---------------- overall status + alert ----------------
    worst = max(sections.values(), key=lambda s: (
        JOURNEY_LEVEL_RANK["completed" if s["completed"] else s["status"]],
        s["updated_at"] if s["status"] != "ok" or s["completed"] else "",
    ))
    worst_level = "completed" if worst["completed"] else worst["status"]

    if worst_level == "critical":
        status = {"tone": "critical", "label": "Maintenance Scheduled",
                  "detail": f"Track maintenance {worst['place']}."}
    elif worst_level == "warning":
        status = {"tone": "warning", "label": "Under Observation",
                  "detail": f"A minor track condition {worst['place']} is being monitored."}
    elif worst_level == "completed":
        status = {"tone": "ok", "label": "Maintenance Completed",
                  "detail": "No active maintenance alert currently affects your route."}
    else:
        status = {"tone": "ok", "label": "Operating Normally",
                  "detail": "No active alerts on your route."}

    critical = [s for s in sections.values() if s["status"] == "critical"]
    if critical:
        critical.sort(key=lambda s: s["updated_at"], reverse=True)
        top = critical[0]
        alert = {
            "active": True,
            "section_id": top["id"],
            "title": "Track Maintenance Alert",
            "message": f"A track-related issue has been identified {top['place']}.",
            "maintenance": "Maintenance action has been scheduled.",
            "impact": "Your journey may experience a temporary delay or speed restriction.",
            "instructions": ["Please follow railway staff instructions."],
            "updated_at": top["updated_at"],
            "more": len(critical) - 1,
        }
    else:
        alert = {"active": False}

    return {
        "ok": True,
        "source": "backend",
        "trains": journey_catalog(),
        "journey": {
            "train_number": train["train_number"],
            "train_name": train["name"],
            "category": passenger_train_category(train["name"]),
            "from_station": names[si],
            "to_station": names[di],
            "current_label": current_label,
            "next_station": next_station,
            "destination": names[di],
            "progress": round(progress, 4),
            "state": state,
            "state_note": note,
            "status": status,
        },
        "route": {"stations": stations, "segments": segments},
        "sections": sections,
        "alert": alert,
        "generated_at": now.isoformat(),
    }


def journey_resolve_selection(user_id, args):
    """Query args win, then the saved choice, then the dashboard default."""
    saved = journey_saved(user_id)

    requested = str(args.get("train", "")).strip()
    if requested in TRAIN_STATE:
        train_number = requested
    elif saved:
        train_number = saved[0]
    else:
        train_number, _ = passenger_selected_train(user_id)

    source = str(args.get("from", "")).strip()
    destination = str(args.get("to", "")).strip()

    if not source and not destination and saved and saved[0] == train_number:
        source, destination = saved[1] or "", saved[2] or ""

    return train_number, source, destination


@app.route("/passenger/journey")
def passenger_journey_page():
    passenger = current_passenger()
    if not passenger:
        return redirect("/passenger")

    return render_template(
        "passenger_journey.html",
        passenger=passenger_context(passenger),
        active="journey",
    )


@app.route("/api/passenger/journey")
def passenger_journey_data():
    passenger = current_passenger()
    if not passenger:
        return jsonify({"ok": False, "error": "Please sign in."}), 401

    train_number, source, destination = journey_resolve_selection(passenger["id"], request.args)
    return jsonify(build_passenger_journey(train_number, source, destination))


@app.route("/api/passenger/journey/select", methods=["POST"])
def passenger_journey_select():
    passenger = current_passenger()
    if not passenger:
        return jsonify({"ok": False, "error": "Please sign in."}), 401

    data = request.get_json(silent=True) or {}
    train_number = str(data.get("train", "")).strip()
    source = str(data.get("from", "")).strip()
    destination = str(data.get("to", "")).strip()

    if train_number not in TRAIN_STATE:
        return jsonify({"ok": False, "error": "Please choose a valid train."}), 400

    names = [stop["name"] for stop in TRAIN_STATE[train_number]["route"]]

    if source not in names or destination not in names:
        return jsonify({"ok": False, "error": "Choose stations that this train stops at."}), 400

    if names.index(source) >= names.index(destination):
        return jsonify({"ok": False, "error": "Destination must come after the source on this train's route."}), 400

    journey_save(passenger["id"], train_number, source, destination)
    return jsonify(build_passenger_journey(train_number, source, destination))


# ============================================================
# SAFETY ALERTS (passenger alert centre)
#
# Same shared data as My Journey: TRAIN_STATE, TICKETS,
# NOTIFICATIONS. The only passenger-owned data is read/unread
# state (passenger_alert_reads). Alerts are never stored; they
# are derived from tickets on every request.
#
# Engineer severity (existing values only) -> passenger label:
#   Normal   -> Normal
#   Abnormal -> Attention
#   Critical -> Critical
# "High" -> "High Attention" is mapped for the day the engineer
# system emits it, but nothing emits it today, so orange never
# appears.
#
# Alert type is derived from ticket state + live train position:
#   Critical, released, Active, section NOT near the train -> Maintenance Activity
#   Critical, released, Active, train is in / entering it  -> Important Safety Alert
#   Abnormal, Active                                       -> Route Attention
#   Critical, released, Resolved (last 24h)                -> Maintenance Completed (history)
#   nothing active                                         -> Route Operating Normally
#
# Only the passenger's selected train, and only tickets inside
# their chosen Source -> Destination stretch, are considered.
# Raw AI fields and ticket ids never leave the server; alert ids
# are one-way hashes.
# ============================================================

ALERTS_HISTORY_HOURS = 24      # how long resolved alerts stay in "Past"
ALERTS_MAX_ATTENTION = 6       # cap on simultaneous "Route Attention" cards
ALERT_EPOCH = datetime.now().isoformat()  # tickets live in memory, so ids restart with the server

ALERT_SEVERITY = {
    "Normal":   {"key": "normal",    "label": "Normal",         "tone": "ok"},
    "Abnormal": {"key": "attention", "label": "Attention",      "tone": "caution"},
    "High":     {"key": "high",      "label": "High Attention", "tone": "high"},
    "Critical": {"key": "critical",  "label": "Critical",       "tone": "alert"},
}
ALERT_SEVERITY_RANK = {"normal": 0, "attention": 1, "high": 2, "critical": 3}

ALERT_NORMAL_TEXT = "No active RailSense alerts currently affect your selected journey."


def alert_hash(*parts):
    raw = "|".join(str(p) for p in (ALERT_EPOCH,) + parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def alert_ticket_state(ticket, now):
    severity = ticket.get("severity")
    status = ticket.get("status")

    if severity == "Critical":
        if not ticket.get("passengers_informed"):
            return None
        if status == "Active":
            return "active"
        if status == "Resolved":
            resolved_at = journey_iso(ticket.get("resolved_at"))
            if resolved_at and (now - resolved_at).total_seconds() <= ALERTS_HISTORY_HOURS * 3600:
                return "resolved"
        return None

    if severity == "Abnormal" and JOURNEY_SHOW_ABNORMAL and status == "Active":
        return "active"

    return None


def alert_is_imminent(kind, index, position):
    """True when the train is inside the section or the section starts at the next station."""
    if kind == "stn":
        return -1e-9 <= index - position <= 1
    return (index <= position < index + 1) or (0 < index - position <= 1)


def alert_content(alert_type, place, location_label, date_text):
    when = f" Planned date: {date_text}." if date_text else ""

    if alert_type == "maintenance":
        return {
            "type_label": "Maintenance Activity", "icon": "wrench",
            "status_label": "Maintenance Scheduled",
            "message": f"Maintenance has been scheduled {place}.{when}",
            "means": "Maintenance activity has been scheduled in this route section.",
            "impact": "Temporary delay or speed restriction is possible.",
            "action": "Follow railway staff instructions and check your journey status before travelling.",
        }
    if alert_type == "critical":
        return {
            "type_label": "Important Safety Alert", "icon": "alert",
            "status_label": "Maintenance Scheduled",
            "message": f"A critical track-related condition has been identified {place}, in your route.",
            "means": "A track-related condition has been identified in a section your train is in or about to enter. Maintenance has been scheduled for it." + when,
            "impact": "Temporary delay or speed restriction is likely.",
            "action": "Please follow railway staff instructions.",
        }
    if alert_type == "attention":
        return {
            "type_label": "Route Attention", "icon": "eye",
            "status_label": "Under Observation",
            "message": "A maintenance-related condition is being monitored along your journey.",
            "means": f"RailSense is keeping the {location_label} under observation. No maintenance action has been announced for it.",
            "impact": "Not expected to affect your journey.",
            "action": "No action needed. Follow railway staff instructions if any are given.",
        }
    return {  # completed
        "type_label": "Maintenance Completed", "icon": "check",
        "status_label": "Maintenance Completed",
        "message": f"Maintenance {place} has been completed.",
        "means": "No active maintenance alert currently affects this section.",
        "impact": "None.",
        "action": "No action needed.",
    }


def alerts_read_keys(user_id):
    try:
        conn = get_db()
        try:
            rows = conn.execute(
                "SELECT alert_key FROM passenger_alert_reads WHERE user_id = ?", (user_id,)
            ).fetchall()
        finally:
            conn.close()
        return {r["alert_key"] for r in rows}
    except Exception as error:
        print("Passenger alert read-state error:", error)
        return set()


def alerts_mark_read(user_id, keys):
    keys = [k for k in keys if k]
    if not keys:
        return
    conn = get_db()
    try:
        now_text = datetime.now().isoformat()
        conn.executemany(
            "INSERT OR IGNORE INTO passenger_alert_reads (user_id, alert_key, read_at) VALUES (?, ?, ?)",
            [(user_id, k, now_text) for k in keys]
        )
        conn.commit()
    finally:
        conn.close()


def build_passenger_alerts(user_id, train_number, source, destination):
    with SIMULATION_LOCK:
        train = dict(TRAIN_STATE[train_number])

    names = [stop["name"] for stop in train["route"]]
    last = len(names) - 1
    si = names.index(source) if source in names else 0
    di = names.index(destination) if destination in names else last
    if si >= di:
        si, di = 0, last

    now = datetime.now()
    position = train["route_index"] + train["position_fraction"]

    with DATABASE_LOCK:
        train_tickets = [dict(t) for t in TICKETS if t["train_number"] == train_number]
        sent_at = {}
        for n in NOTIFICATIONS:
            if n.get("type") == "passengers" and n.get("status") == "SENT":
                sent_at[n.get("ticket_id")] = max(
                    sent_at.get(n.get("ticket_id"), ""), n.get("created_at", "")
                )

    # ---------- group: one alert per (kind, state, location) ----------
    groups = {}
    for ticket in train_tickets:
        state = alert_ticket_state(ticket, now)
        if state is None:
            continue

        target = journey_ticket_target(ticket, names)
        if target is None:
            continue

        kind, index = target
        inside = (si <= index <= di) if kind == "stn" else (si <= index and index + 1 <= di)
        if not inside:
            continue

        critical = ticket["severity"] == "Critical"
        if state == "resolved":
            stamp = ticket.get("resolved_at") or ticket["created_at"]
        elif critical:
            stamp = sent_at.get(ticket["id"]) or ticket["created_at"]
        else:
            stamp = ticket["created_at"]

        key = ("critical" if critical else "watch", state, ticket["location_name"])
        g = groups.setdefault(key, {
            "kind": kind, "index": index, "state": state, "critical": critical,
            "first_id": ticket["id"], "stamp": stamp, "date": "",
        })
        g["first_id"] = min(g["first_id"], ticket["id"])
        if stamp >= g["stamp"]:
            g["stamp"] = stamp
            if ticket.get("maintenance_date"):
                g["date"] = ticket["maintenance_date"]

    read_keys = alerts_read_keys(user_id)
    alerts = []

    for (_, state, location_name), g in groups.items():
        kind, index = g["kind"], g["index"]
        _, place = passenger_place(location_name)
        label = names[index] if kind == "stn" else f"{names[index]} \u2013 {names[index + 1]}"
        location_label = f"{label} section"

        if state == "resolved":
            alert_type, severity = "completed", ALERT_SEVERITY["Normal"]
        elif g["critical"]:
            imminent = alert_is_imminent(kind, index, position)
            alert_type = "critical" if imminent else "maintenance"
            severity = ALERT_SEVERITY["Critical"]
        else:
            alert_type, severity = "attention", ALERT_SEVERITY["Abnormal"]

        content = alert_content(alert_type, place, location_label, format_maintenance_date(g["date"]))
        group_id = alert_hash("group", train_number, g["first_id"])
        alert_id = alert_hash("read", train_number, g["first_id"], state, alert_type)

        alerts.append({
            "id": alert_id,
            "group": group_id,
            "type": alert_type,
            "state": state,
            "train_number": train_number,
            "train_name": train["name"],
            "location": location_label,
            "severity": severity,
            "time": g["stamp"],
            "unread": alert_id not in read_keys,
            **content,
        })

    # ---------- cap noisy "Route Attention" cards ----------
    watch = sorted([a for a in alerts if a["type"] == "attention"], key=lambda a: a["time"], reverse=True)
    hidden_ids = {a["id"] for a in watch[ALERTS_MAX_ATTENTION:]}
    alerts = [a for a in alerts if a["id"] not in hidden_ids]

    alerts.sort(key=lambda a: (
        0 if a["state"] == "active" else 1,
        -ALERT_SEVERITY_RANK[a["severity"]["key"]],
        0 if a["type"] == "critical" else 1,
        _neg_time(a["time"]),
    ))

    active = [a for a in alerts if a["state"] == "active"]
    worst = max((ALERT_SEVERITY_RANK[a["severity"]["key"]] for a in active), default=0)

    if not active:
        summary = {"level": "normal", "headline": "Route Operating Normally", "text": ALERT_NORMAL_TEXT}
    elif worst >= ALERT_SEVERITY_RANK["critical"]:
        summary = {
            "level": "critical",
            "headline": f"{len(active)} active alert{'s' if len(active) != 1 else ''} on your journey",
            "text": "Review the details below and follow railway staff instructions.",
        }
    else:
        summary = {
            "level": "attention",
            "headline": f"{len(active)} active alert{'s' if len(active) != 1 else ''} on your journey",
            "text": "RailSense is monitoring your route. Details are below.",
        }

    summary.update({
        "active": len(active),
        "past": len(alerts) - len(active),
        "unread": sum(1 for a in alerts if a["unread"]),
    })

    return {
        "ok": True,
        "journey": {
            "train_number": train_number,
            "train_name": train["name"],
            "from_station": names[si],
            "to_station": names[di],
        },
        "summary": summary,
        "alerts": alerts,
        "hidden_attention": len(hidden_ids),
        "generated_at": now.isoformat(),
    }


def _neg_time(value):
    parsed = journey_iso(value)
    return -parsed.timestamp() if parsed else 0


def alerts_unread_count(user_id):
    try:
        train_number, source, destination = journey_resolve_selection(user_id, {})
        return build_passenger_alerts(user_id, train_number, source, destination)["summary"]["unread"]
    except Exception as error:
        print("Passenger unread-alert count error:", error)
        return 0


@app.route("/passenger/alerts")
def passenger_alerts_page():
    passenger = current_passenger()
    if not passenger:
        return redirect("/passenger")

    return render_template(
        "passenger_alerts.html",
        passenger=passenger_context(passenger),
        active="alerts",
    )


@app.route("/api/passenger/alerts")
def passenger_alerts_data():
    passenger = current_passenger()
    if not passenger:
        return jsonify({"ok": False, "error": "Please sign in."}), 401

    train_number, source, destination = journey_resolve_selection(passenger["id"], request.args)
    return jsonify(build_passenger_alerts(passenger["id"], train_number, source, destination))


@app.route("/api/passenger/alerts/read", methods=["POST"])
def passenger_alerts_read():
    passenger = current_passenger()
    if not passenger:
        return jsonify({"ok": False, "error": "Please sign in."}), 401

    data = request.get_json(silent=True) or {}
    train_number, source, destination = journey_resolve_selection(
        passenger["id"],
        {"train": data.get("train", ""), "from": data.get("from", ""), "to": data.get("to", "")},
    )

    current = build_passenger_alerts(passenger["id"], train_number, source, destination)["alerts"]
    valid = {a["id"] for a in current}

    if data.get("all"):
        wanted = valid
    else:
        wanted = {str(i) for i in (data.get("ids") or [])} & valid

    alerts_mark_read(passenger["id"], list(wanted))
    return jsonify(build_passenger_alerts(passenger["id"], train_number, source, destination))


# ============================================================
# PASSENGER CHAT ROOM
#
# One room per train. A passenger is always placed in the room
# of their selected journey train (the same train My Journey and
# Safety Alerts use), so passengers on unrelated trains never mix.
#
# Messages live in SQLite (passenger_chat_messages), so they
# survive restarts. The browser polls every few seconds; the
# response shape (messages since an id, online count, system
# messages) is deliberately the same shape a WebSocket push would
# carry, so a socket layer can replace the polling later without
# touching the page.
#
# Two kinds of timeline entries:
#   * passenger messages - stored, shown as "First L." only
#   * RailSense System messages - NEVER stored and never typed by
#     a passenger. They are derived on every request from the same
#     alerts the Safety Alerts page uses (so a Critical ticket only
#     appears once the engineer has chosen "Inform passengers").
#
# Raw AI fields (defect class, confidence, probabilities, audio /
# image evidence, Grad-CAM) and ticket ids are never included.
#
# "Online" = passengers who have had this room open within the
# last CHAT_ONLINE_SECONDS. It is a real count, not a fake one.
# ============================================================

CHAT_MAX_LENGTH = 500
CHAT_HISTORY_LIMIT = 60        # messages sent on first load
CHAT_BATCH_LIMIT = 100         # max messages per poll
CHAT_KEEP_PER_ROOM = 500       # older messages are trimmed
CHAT_ONLINE_SECONDS = 20
CHAT_RATE_WINDOW = 10          # seconds
CHAT_RATE_MAX = 5              # messages per window per passenger
CHAT_SYSTEM_MAX = 8            # newest system cards shown
CHAT_SYSTEM_TYPES = ("critical", "maintenance", "completed")

CHAT_PRESENCE = {}             # train_number -> {user_id: last_seen_epoch}
CHAT_RECENT_SENDS = {}         # user_id -> [epoch, ...]
CHAT_LOCK = threading.Lock()


def chat_display_name(name):
    """'Tejashree Patil' -> 'Tejashree P.' (passengers are never shown in full)."""
    parts = [p for p in str(name or "").split() if p]
    if not parts:
        return "Passenger"
    if len(parts) == 1:
        return parts[0][:24]
    return f"{parts[0][:20]} {parts[-1][0].upper()}."


def chat_clean_text(value):
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(ch for ch in text if ch == "\n" or ch.isprintable())
    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")
    return text.strip()


def chat_touch_presence(train_number, user_id):
    now = time.time()
    with CHAT_LOCK:
        room = CHAT_PRESENCE.setdefault(train_number, {})
        room[user_id] = now
        for uid in [u for u, seen in room.items() if now - seen > CHAT_ONLINE_SECONDS]:
            del room[uid]
        return len(room)


def chat_fetch_messages(train_number, since, user_id):
    conn = get_db()
    try:
        if since > 0:
            rows = conn.execute(
                "SELECT id, user_id, sender_name, body, created_at "
                "FROM passenger_chat_messages "
                "WHERE train_number = ? AND id > ? ORDER BY id ASC LIMIT ?",
                (train_number, since, CHAT_BATCH_LIMIT)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, user_id, sender_name, body, created_at "
                "FROM passenger_chat_messages "
                "WHERE train_number = ? ORDER BY id DESC LIMIT ?",
                (train_number, CHAT_HISTORY_LIMIT)
            ).fetchall()
            rows = list(reversed(rows))
    finally:
        conn.close()

    return [
        {
            "id": r["id"],
            "name": r["sender_name"],
            "text": r["body"],
            "time": r["created_at"],
            "mine": r["user_id"] == user_id,
        }
        for r in rows
    ]


def chat_system_messages(alerts):
    """Passenger-safe RailSense System messages derived from the shared alerts."""
    items = []
    for a in alerts:
        if a["type"] not in CHAT_SYSTEM_TYPES:
            continue
        items.append({
            "id": "sys-" + a["id"],
            "kind": a["type"],
            "state": a["state"],
            "title": a["type_label"],
            "text": a["message"],
            "action": a["action"],
            "location": a["location"],
            "time": a["time"],
        })

    items.sort(key=lambda m: m["time"], reverse=True)
    items = items[:CHAT_SYSTEM_MAX]
    items.reverse()
    return items


@app.route("/passenger/chat")
def passenger_chat_page():
    passenger = current_passenger()
    if not passenger:
        return redirect("/passenger")

    return render_template(
        "passenger_chat.html",
        passenger=passenger_context(passenger),
        active="chat",
    )


@app.route("/api/passenger/chat")
def passenger_chat_data():
    passenger = current_passenger()
    if not passenger:
        return jsonify({"ok": False, "error": "Please sign in."}), 401

    user_id = passenger["id"]
    train_number, source, destination = journey_resolve_selection(user_id, {})

    try:
        since = max(0, int(request.args.get("since", 0)))
    except (TypeError, ValueError):
        since = 0

    built = build_passenger_alerts(user_id, train_number, source, destination)
    journey = built["journey"]
    with SIMULATION_LOCK:
        train_name = TRAIN_STATE[train_number]["name"]

    online = chat_touch_presence(train_number, user_id)
    messages = chat_fetch_messages(train_number, since, user_id)

    active_alerts = [a for a in built["alerts"] if a["state"] == "active" and a["type"] in ("critical", "maintenance")]

    return jsonify({
        "ok": True,
        "room": {
            "train_number": train_number,
            "train_name": train_name,
            "category": passenger_train_category(train_name),
            "from_station": journey["from_station"],
            "to_station": journey["to_station"],
            "journey_chosen": journey_saved(user_id) is not None,
        },
        "online": online,
        "max_length": CHAT_MAX_LENGTH,
        "messages": messages,
        "system": chat_system_messages(built["alerts"]),
        "active_alert": (
            {"title": active_alerts[0]["type_label"], "location": active_alerts[0]["location"]}
            if active_alerts else None
        ),
        "generated_at": datetime.now().isoformat(),
    })


@app.route("/api/passenger/chat/send", methods=["POST"])
def passenger_chat_send():
    passenger = current_passenger()
    if not passenger:
        return jsonify({"ok": False, "error": "Please sign in."}), 401

    user_id = passenger["id"]
    data = request.get_json(silent=True) or {}
    text = chat_clean_text(data.get("text"))

    if not text:
        return jsonify({"ok": False, "error": "Type a message first."}), 400
    if len(text) > CHAT_MAX_LENGTH:
        return jsonify({
            "ok": False,
            "error": f"Messages can be up to {CHAT_MAX_LENGTH} characters.",
        }), 400

    now = time.time()
    with CHAT_LOCK:
        recent = [t for t in CHAT_RECENT_SENDS.get(user_id, []) if now - t < CHAT_RATE_WINDOW]
        if len(recent) >= CHAT_RATE_MAX:
            CHAT_RECENT_SENDS[user_id] = recent
            return jsonify({
                "ok": False,
                "error": "You are sending messages too quickly. Please wait a moment.",
            }), 429
        recent.append(now)
        CHAT_RECENT_SENDS[user_id] = recent

    # The room is always the passenger's own selected train.
    train_number, _, _ = journey_resolve_selection(user_id, {})
    sender = chat_display_name(passenger.get("name"))
    created_at = datetime.now().isoformat()

    conn = get_db()
    try:
        cursor = conn.execute(
            "INSERT INTO passenger_chat_messages "
            "(train_number, user_id, sender_name, body, created_at) VALUES (?, ?, ?, ?, ?)",
            (train_number, user_id, sender, text, created_at)
        )
        message_id = cursor.lastrowid
        conn.execute(
            "DELETE FROM passenger_chat_messages WHERE train_number = ? AND id NOT IN ("
            "SELECT id FROM passenger_chat_messages WHERE train_number = ? "
            "ORDER BY id DESC LIMIT ?)",
            (train_number, train_number, CHAT_KEEP_PER_ROOM)
        )
        conn.commit()
    finally:
        conn.close()

    chat_touch_presence(train_number, user_id)

    return jsonify({
        "ok": True,
        "message": {
            "id": message_id,
            "name": sender,
            "text": text,
            "time": created_at,
            "mine": True,
        },
    })


# ============================================================
# EMERGENCY HELP (passenger)
#
# Defaults let the Emergency Help cards work without editing any
# other project file. Values in .env override these defaults.
# ROUTING_OFFICER_PHONE is an internal operations contact and is
# deliberately never displayed to passengers.
#
# Defaults: 112 = India emergency response; 139 = Indian Railways
# assistance; 101 = fire; 108 = ambulance (availability may vary by
# state/service). Confirm local/official contact details before a
# production launch. Set an env variable to an empty value only if
# you intentionally want that card to show as unavailable.
# ============================================================

EMERGENCY_CARDS = [
    {
        "key": "railway_police", "icon": "police", "env": "EMERGENCY_RAILWAY_POLICE_PHONE",
        "default": "112",
        "title": "Railway Police",
        "text": "For security or passenger safety emergencies.",
    },
    {
        "key": "medical", "icon": "medical", "env": "EMERGENCY_MEDICAL_PHONE",
        "default": "108",
        "title": "Medical Emergency",
        "text": "For urgent medical assistance.",
    },
    {
        "key": "fire", "icon": "fire", "env": "EMERGENCY_FIRE_PHONE",
        "default": "101",
        "title": "Fire Emergency",
        "text": "For fire or smoke emergencies.",
    },
    {
        "key": "railway_emergency", "icon": "siren", "env": "EMERGENCY_RAILWAY_PHONE",
        "default": "139",
        "title": "Railway Emergency",
        "text": "For urgent railway-related assistance.",
    },
]


def emergency_parse_number(value):
    """Return (display, tel) for a usable number, or ("", "") if empty/invalid.

    Accepts digits with optional leading '+', spaces, hyphens and brackets,
    3-15 digits in total. Anything else (letters, scripts, URLs) is rejected so
    a bad .env value can never turn into a misleading tel: link.
    """
    text = str(value or "").strip()
    if not text:
        return "", ""

    if any(ch not in "0123456789+ -()" for ch in text):
        return "", ""

    plus = text.startswith("+")
    digits = "".join(ch for ch in text if ch.isdigit())

    if "+" in text[1:] or not (3 <= len(digits) <= 15):
        return "", ""

    return text, ("+" if plus else "") + digits


def emergency_cards():
    cards = []
    for item in EMERGENCY_CARDS:
        # A non-empty .env value takes priority; otherwise use the app default.
        configured_value = os.getenv(item["env"]) or item["default"]
        display, tel = emergency_parse_number(configured_value)
        cards.append({
            "key": item["key"],
            "icon": item["icon"],
            "title": item["title"],
            "text": item["text"],
            "display": display,
            "tel": tel,
            "configured": bool(tel),
        })
    return cards


@app.route("/passenger/emergency")
def passenger_emergency_page():
    passenger = current_passenger()
    if not passenger:
        return redirect("/passenger")

    cards = emergency_cards()

    return render_template(
        "passenger_emergency.html",
        passenger=passenger_context(passenger),
        active="emergency",
        cards=cards,
        any_configured=any(c["configured"] for c in cards),
    )


# ============================================================
# REPORT ISSUE / MY REPORTS  <->  ENGINEER DASHBOARD
#
# Passenger -> Report Issue -> passenger_reports table -> Engineer
# dashboard ("Passenger reports" panel). The engineer moves a report
# Submitted -> Under Review -> Resolved and the passenger sees that
# status in My Reports. Same database, same rows, two views.
#
# A report is NOT an engineer ticket and cannot create one. Passengers
# only ever read their own rows; only an engineer session can list all
# reports or change a status (see require_engineer above).
# ============================================================

REPORT_ISSUE_TYPES = [
    "Unusual Sound",
    "Track Condition",
    "Vibration or Rough Ride",
    "Smoke or Burning Smell",
    "Damaged Equipment",
    "Other",
]
REPORT_STATUSES = ["Submitted", "Under Review", "Resolved"]
REPORT_MAX_DESCRIPTION = 500
REPORT_LIMIT_COUNT = 5          # reports allowed ...
REPORT_LIMIT_SECONDS = 3600     # ... per passenger per hour


def report_locations(train):
    """Every station plus every station-to-station stretch of a train."""
    names = [stop["name"] for stop in train["route"]]
    places = list(names)
    for index in range(len(names) - 1):
        places.append(f"{names[index]} \u2192 {names[index + 1]}")
    return places


def report_options(selected_train):
    with SIMULATION_LOCK:
        trains = [dict(t) for t in TRAIN_STATE.values()]
    return {
        "trains": [
            {
                "train_number": t["train_number"],
                "name": t["name"],
                "locations": report_locations(t),
            }
            for t in trains
        ],
        "issue_types": REPORT_ISSUE_TYPES,
        "selected": selected_train,
        "max_description": REPORT_MAX_DESCRIPTION,
    }


def report_public(row):
    """What a passenger may see of their own report."""
    return {
        "code": row["code"],
        "train_number": row["train_number"],
        "train_name": row["train_name"],
        "location": row["location"],
        "issue_type": row["issue_type"],
        "description": row["description"],
        "status": row["status"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


@app.route("/passenger/report")
def passenger_report_page():
    passenger = current_passenger()
    if not passenger:
        return redirect("/passenger")

    selected, _ = passenger_selected_train(passenger["id"])

    return render_template(
        "passenger_report.html",
        passenger=passenger_context(passenger),
        active="report",
        options=report_options(selected),
    )


@app.route("/passenger/reports")
def passenger_reports_page():
    passenger = current_passenger()
    if not passenger:
        return redirect("/passenger")

    return render_template(
        "passenger_reports.html",
        passenger=passenger_context(passenger),
        active="reports",
    )


@app.route("/api/passenger/reports", methods=["GET"])
def passenger_reports_list():
    passenger = current_passenger()
    if not passenger:
        return jsonify({"ok": False, "error": "Please sign in."}), 401

    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM passenger_reports WHERE user_id = ? ORDER BY id DESC",
            (passenger["id"],)
        ).fetchall()
    finally:
        conn.close()

    return jsonify({
        "ok": True,
        "reports": [report_public(r) for r in rows],
        "generated_at": datetime.now().isoformat(),
    })


@app.route("/api/passenger/reports", methods=["POST"])
def passenger_reports_create():
    passenger = current_passenger()
    if not passenger:
        return jsonify({"ok": False, "error": "Please sign in."}), 401

    data = request.get_json(silent=True) or {}

    train_number = str(data.get("train", "")).strip()
    location = str(data.get("location", "")).strip()
    issue_type = str(data.get("issue_type", "")).strip()
    description = " ".join(str(data.get("description", "")).split())

    errors = {}

    with SIMULATION_LOCK:
        train = dict(TRAIN_STATE[train_number]) if train_number in TRAIN_STATE else None

    if train is None:
        errors["train"] = "Choose the train you are travelling on."
    elif location not in report_locations(train):
        errors["location"] = "Choose where on the route you noticed it."

    if issue_type not in REPORT_ISSUE_TYPES:
        errors["issue_type"] = "Choose what you noticed."

    if len(description) > REPORT_MAX_DESCRIPTION:
        errors["description"] = (
            f"Please keep details under {REPORT_MAX_DESCRIPTION} characters."
        )

    if errors:
        return jsonify({"ok": False, "errors": errors}), 400

    now = datetime.now()

    conn = get_db()
    try:
        since = datetime.fromtimestamp(
            now.timestamp() - REPORT_LIMIT_SECONDS
        ).isoformat()
        recent = conn.execute(
            "SELECT COUNT(*) FROM passenger_reports "
            "WHERE user_id = ? AND created_at >= ?",
            (passenger["id"], since)
        ).fetchone()[0]

        if recent >= REPORT_LIMIT_COUNT:
            return jsonify({
                "ok": False,
                "error": (
                    "You have sent several reports recently. "
                    "Please wait a while before sending another. "
                    "If this is urgent, use Emergency Help."
                ),
            }), 429

        cursor = conn.execute(
            "INSERT INTO passenger_reports "
            "(user_id, passenger_name, train_number, train_name, location, "
            " issue_type, description, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'Submitted', ?, ?)",
            (
                passenger["id"],
                passenger.get("name", "Passenger"),
                train["train_number"],
                train["name"],
                location,
                issue_type,
                description,
                now.isoformat(),
                now.isoformat(),
            )
        )
        code = f"RS-{now.year}-{cursor.lastrowid:05d}"
        conn.execute(
            "UPDATE passenger_reports SET code = ? WHERE id = ?",
            (code, cursor.lastrowid)
        )
        conn.commit()
    finally:
        conn.close()

    return jsonify({
        "ok": True,
        "code": code,
        "message": "Your report has been sent to the railway engineers.",
    }), 201


@app.route("/api/engineer/reports", methods=["GET"])
def engineer_reports_list():
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM passenger_reports ORDER BY id DESC LIMIT 200"
        ).fetchall()
    finally:
        conn.close()

    reports = []
    for r in rows:
        item = report_public(r)
        item["id"] = r["id"]
        item["passenger_name"] = r["passenger_name"]
        reports.append(item)

    return jsonify({
        "ok": True,
        "statuses": REPORT_STATUSES,
        "open_count": len([r for r in reports if r["status"] != "Resolved"]),
        "reports": reports,
    })


@app.route("/api/engineer/reports/<int:report_id>/status", methods=["POST"])
def engineer_reports_set_status(report_id):
    data = request.get_json(silent=True) or {}
    status = str(data.get("status", "")).strip()

    if status not in REPORT_STATUSES:
        return jsonify({"ok": False, "message": "Unknown report status."}), 400

    conn = get_db()
    try:
        cursor = conn.execute(
            "UPDATE passenger_reports SET status = ?, updated_at = ? WHERE id = ?",
            (status, datetime.now().isoformat(), report_id)
        )
        conn.commit()
        found = cursor.rowcount > 0
    finally:
        conn.close()

    if not found:
        return jsonify({"ok": False, "message": "Report not found."}), 404

    return jsonify({"ok": True, "status": status, "message": f"Report marked {status}."})


# ============================================================
# ERROR HANDLER
# ============================================================

@app.errorhandler(404)
def not_found(error):
    return jsonify({
        "ok": False,
        "error": "Endpoint not found."
    }), 404


# ============================================================
# START SERVER
# ============================================================

if __name__ == "__main__":
    print()
    print("=" * 60)
    print("RAILSENSE FLASK SERVER")
    print("=" * 60)
    print("Server running at:")
    print("http://127.0.0.1:5000")
    print()
    print("Engineer login:")
    print("http://127.0.0.1:5000/engineer-login")
    print()
    print("Engineer dashboard:")
    print("http://127.0.0.1:5000/engineer")
    print()
    print("SVM API:")
    print("POST /api/ai/audio/predict")
    print()
    print("Log-Mel Spectrogram API:")
    print("POST /api/ai/audio/spectrogram")
    print()
    print("Simulation:")
    print("POST /api/simulation/start")
    print("GET  /api/simulation/status")
    print("POST /api/simulation/stop")
    print()
    print("Audio testing directory:")
    print(AUDIO_TESTING_PATH)
    print()
    print("Image testing directory:")
    print(IMAGE_TESTING_PATH)
    print("=" * 60)

    app.run(
        host="127.0.0.1",
        port=5000,
        debug=True,
        threaded=True,
        use_reloader=False
    )
