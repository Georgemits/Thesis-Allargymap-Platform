# AllergyMap Platform

Crowdsensing web platform for monitoring allergic diseases and their correlation
with environmental factors (pollen, air quality, weather) in Greece.

## Project Structure

```
Thesis-Allargymap-Platform/
├── backend/                        # Flask REST API
│   ├── run.py                      # Entry point (dev) / gunicorn target
│   ├── requirements.txt
│   ├── .env.example                # Copy to .env and fill in values
│   └── app/
│       ├── __init__.py             # App factory (create_app)
│       ├── config.py               # Development / Production configs
│       ├── extensions.py           # PyMongo singleton
│       ├── models/
│       │   ├── report.py           # Symptom report schema + builder
│       │   └── env_snapshot.py     # Environmental snapshot schema (docs)
│       ├── routes/
│       │   ├── health.py           # GET /health
│       │   ├── reports.py          # POST/GET /api/reports
│       │   └── env_data.py         # GET /api/env/latest, /api/env/city/<city>
│       └── utils/
│           └── geo.py              # Haversine distance, nearest city lookup
│
├── frontend/                       # Static HTML/JS/CSS (served by nginx in Docker)
│   ├── index.html                  # Leaflet map with symptom heatmap
│   ├── report.html                 # VAS symptom report form
│   ├── dashboard.html              # Chart.js charts for env + report data
│   ├── css/
│   │   └── style.css
│   └── js/
│       ├── api.js                  # Fetch wrapper (global `API` object)
│       ├── map.js                  # Leaflet map logic
│       ├── report.js               # Form submission + geolocation
│       └── dashboard.js            # Chart rendering
│
├── docker/
│   ├── docker-compose.yml          # Flask + MongoDB + nginx
│   ├── Dockerfile.backend
│   └── mongo-init/
│       └── init.js                 # Index creation on first start
│
└── data_collection/                # Open-Meteo data fetcher (pre-existing)
    ├── open_meteo_fetcher.py
    ├── requirements.txt
    └── output/                     # CSV / JSON output
```

## Quick Start

### 1. Run with Docker (recommended)

```bash
cd docker
cp ../backend/.env.example .env   # edit SECRET_KEY if needed
docker compose up --build
```

| Service  | URL                    |
|----------|------------------------|
| Frontend | http://localhost:8080  |
| Backend  | http://localhost:5000  |
| MongoDB  | localhost:27017        |

### 2. Run locally (development)

**Backend:**
```bash
cd backend
python -m venv .venv && source .venv/bin/activate   # or .venv\Scripts\activate on Windows
pip install -r requirements.txt
cp .env.example .env   # edit as needed
python run.py
```

**Frontend:**
Open `frontend/index.html` in a browser directly, or serve with any static server:
```bash
python -m http.server 8080 --directory frontend
```

## API Endpoints

| Method | Path                          | Description                              |
|--------|-------------------------------|------------------------------------------|
| GET    | `/health`                     | Health check                             |
| POST   | `/api/reports/`               | Submit symptom report                    |
| GET    | `/api/reports/`               | List reports (`?city=&limit=`)           |
| GET    | `/api/reports/heatmap`        | GeoJSON heatmap (`?days=30`)             |
| GET    | `/api/env/latest`             | Latest env snapshot per city             |
| GET    | `/api/env/city/<city>`        | Time series for a city (`?days=7`)       |

## Data Collection

Populate `env_snapshots` in MongoDB by running the fetcher:

```bash
cd data_collection
python open_meteo_fetcher.py --mode forecast   # all Greek cities
```

A scheduled import script (cron / Celery) to push fetcher output into MongoDB
is planned for a future iteration.

## Tech Stack

| Layer       | Technology                              |
|-------------|-----------------------------------------|
| Backend     | Python 3.12, Flask 3, Flask-PyMongo     |
| Database    | MongoDB 7 (GeoJSON 2dsphere indexes)    |
| Frontend    | Vanilla JS, Leaflet.js, Chart.js        |
| Container   | Docker Compose (Flask + Mongo + nginx)  |
| Data source | Open-Meteo API (weather, pollen, AQ)    |
