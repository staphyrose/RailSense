"""
timetable.py — the single source of truth linking:
  - which of your 8 trains is running which route, at what time
  - where that train physically is right now (for the engineer map)
  - how a passenger's source + destination + timing maps to a train_number
    (for the "track health for my journey" feature)

Keeping this fixed (8 trains, real-ish suburban schedule) is what makes the
location problem tractable for a college-project simulation, per your plan.
Add more trains later by just adding more entries below — nothing else
needs to change.
"""
from datetime import datetime, timedelta

# ---------------------------------------------------------------------
# Physical route: Panvel <-> Thane, as real-ish lat/lng endpoints.
# Position along the route is interpolated between these two points.
# ---------------------------------------------------------------------
ROUTE = {
    "name": "Panvel-Thane",
    "panvel": (18.9894, 73.1175),
    "thane": (19.1943, 72.9739),
    "distance_km": 34,
}

# ---------------------------------------------------------------------
# 8 trains, alternating direction, spread across a morning schedule.
# departure_time is "HH:MM" (24h). travel_minutes is how long a one-way
# trip takes — used to compute where the train is right now.
# ---------------------------------------------------------------------
TRAINS = [
    {"train_number": "91001", "direction": "UP",   "source": "Panvel", "destination": "Thane", "departure_time": "07:00", "travel_minutes": 55},
    {"train_number": "91002", "direction": "DOWN", "source": "Thane",  "destination": "Panvel", "departure_time": "07:15", "travel_minutes": 55},
    {"train_number": "91003", "direction": "UP",   "source": "Panvel", "destination": "Thane", "departure_time": "07:30", "travel_minutes": 55},
    {"train_number": "91004", "direction": "DOWN", "source": "Thane",  "destination": "Panvel", "departure_time": "07:45", "travel_minutes": 55},
    {"train_number": "91005", "direction": "UP",   "source": "Panvel", "destination": "Thane", "departure_time": "08:00", "travel_minutes": 55},
    {"train_number": "91006", "direction": "DOWN", "source": "Thane",  "destination": "Panvel", "departure_time": "08:15", "travel_minutes": 55},
    {"train_number": "91007", "direction": "UP",   "source": "Panvel", "destination": "Thane", "departure_time": "08:30", "travel_minutes": 55},
    {"train_number": "91008", "direction": "DOWN", "source": "Thane",  "destination": "Panvel", "departure_time": "08:45", "travel_minutes": 55},
]


def _interpolate(km_from_source: float, direction: str):
    """Turn distance-travelled into a lat/lng point."""
    frac = max(0.0, min(1.0, km_from_source / ROUTE["distance_km"]))
    if direction == "DOWN":
        frac = 1 - frac  # Thane -> Panvel runs the route backwards
    start = ROUTE["panvel"]
    end = ROUTE["thane"]
    lat = start[0] + (end[0] - start[0]) * frac
    lng = start[1] + (end[1] - start[1]) * frac
    return lat, lng


def get_live_position(train, sim_minutes_elapsed: float):
    """
    Where is this train right now, given how many simulated minutes have
    elapsed since the simulation started? Loops the trip once it arrives.
    """
    progress = (sim_minutes_elapsed % train["travel_minutes"]) / train["travel_minutes"]
    km = progress * ROUTE["distance_km"]
    lat, lng = _interpolate(km, train["direction"])
    return {"km": round(km, 2), "lat": lat, "lng": lng}


def find_train_for_journey(source: str, destination: str, timing: str):
    """
    Passenger enters source, destination, and roughly when their train
    leaves (HH:MM). Returns the best-matching train_number, or None.
    This is what synchronizes the passenger's "track health" request with
    a specific train the engineer side is actually tracking.
    """
    source = (source or "").strip().lower()
    destination = (destination or "").strip().lower()

    candidates = [
        t for t in TRAINS
        if t["source"].lower() == source and t["destination"].lower() == destination
    ]
    if not candidates:
        return None

    if not timing:
        return candidates[0]["train_number"]

    try:
        target = datetime.strptime(timing, "%H:%M")
    except ValueError:
        return candidates[0]["train_number"]

    def time_diff(t):
        dep = datetime.strptime(t["departure_time"], "%H:%M")
        return abs((dep - target).total_seconds())

    best = min(candidates, key=time_diff)
    return best["train_number"]


def get_train(train_number: str):
    for t in TRAINS:
        if t["train_number"] == train_number:
            return t
    return None
