# Rail Safety MVP

## 1. Install
python -m venv .venv
# Windows:
.venv\Scripts\activate
pip install -r requirements.txt

## 2. Start backend
cd backend
uvicorn main:app --reload

## 3. Open frontend
Open frontend/index.html in the browser.

Features in this MVP:
- Live train data
- Passenger location
- Defect reports
- Defect hotspot aggregation
- Advisory endpoint
- Emergency contacts UI
- Route-wide WebSocket chat
- Journey companion token
- SQLite persistence

Important:
This is a college-project MVP. Emergency numbers, train data, authentication,
moderation, real railway integrations and production security must be verified
before any real-world deployment.
