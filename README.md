# AllergyMap Platform

A crowdsensing web platform for monitoring allergic diseases and their correlation
with environmental factors (pollen, air quality, weather) in Greece.

Built as part of a university thesis. Users report their allergy symptoms through
a simple form, which gets combined with real environmental data (pollen, humidity,
dust, temperature) to produce live heatmaps and dashboards.

---

## What's Inside

```
Thesis-Allargymap-Platform/
│
├── backend/                   ← Flask REST API (Python)
│   ├── run.py                 ← Start the server with: python run.py
│   ├── requirements.txt       ← Python packages needed
│   ├── .env.example           ← Copy this to .env before running (incl. GOOGLE_POLLEN_API_KEY)
│   ├── README.md               ← Backend-specific notes
│   └── app/
│       ├── __init__.py        ← App setup (registers all routes)
│       ├── config.py          ← Dev / Production settings
│       ├── extensions.py      ← MongoDB connection
│       ├── models/            ← Data structure definitions
│       │   ├── report.py      ← Symptom report (what users submit)
│       │   └── env_snapshot.py← Environmental data (pollen, weather)
│       ├── routes/
│       │   ├── health.py      ← GET /health  (just checks the server is alive)
│       │   ├── reports.py     ← POST/GET /api/reports  (user symptom reports)
│       │   └── env_data.py    ← GET /api/env/...       (environmental data)
│       └── utils/
│           └── geo.py         ← Location helper functions
│
├── frontend/                  ← The website (plain HTML + JavaScript)
│   ├── index.html             ← Main page: Leaflet map with allergen heatmap
│   ├── report.html            ← Form where users submit their symptoms (VAS tool)
│   ├── dashboard.html         ← Charts for environmental + symptom data, date-range picker
│   ├── css/style.css          ← Clinical dark theme (WCAG AA contrast-checked, see frontend/README.md)
│   ├── README.md               ← Frontend-specific notes (design system, contrast notes)
│   └── js/
│       ├── api.js             ← Handles all communication with the backend
│       ├── map.js             ← Leaflet allergen-concentration heatmap (olive/grass/ragweed/dust)
│       ├── navbar.js           ← Shared mobile nav toggle
│       ├── report.js          ← Handles form submission + GPS location
│       └── dashboard.js       ← Draws the charts
│
├── docker/                    ← Run everything with one command
│   ├── docker-compose.yml     ← Starts Flask + MongoDB + nginx together
│   ├── Dockerfile.backend     ← How to build the backend container
│   ├── README.md               ← Docker-specific notes (env vars, ports)
│   └── mongo-init/init.js     ← Sets up the database on first run
│
└── data_collection/           ← Fetches real environmental data
    ├── open_meteo_fetcher.py   ← Weather + air quality + pollen (grains/m3) from Open-Meteo
    ├── google_pollen_fetcher.py ← Daily pollen UPI (0-5) from the Google Pollen API (primary source)
    ├── pollen_source.py         ← Normalizes both providers; Google primary, Open-Meteo fallback
    ├── mongo_importer.py        ← Imports combined CSVs into MongoDB
    ├── predictor.py              ← RandomForest forecasting (used by /api/predictions)
    ├── tests/                    ← Unit tests (HTTP mocked)
    ├── README.md                 ← Data-collection-specific notes (hybrid pollen source, scale mapping)
    ├── requirements.txt
    └── output/                ← CSVs and JSON saved here after each fetch
```

---

## How to Run It (Step by Step)

There are two ways. **Docker is the easiest** — it handles everything automatically.
The manual method is better if you want to develop and see code changes instantly.

---

### Option A — Docker (Recommended, easiest)

Docker runs all three services (database, backend, frontend) together with a single command.

**Step 1 — Install Docker Desktop**

Download and install Docker Desktop from https://www.docker.com/products/docker-desktop/

After installation, open Docker Desktop and make sure it's running (you'll see the
whale icon in your taskbar). You don't need to create an account.

**Step 2 — Open a terminal in the right folder**

Open PowerShell (or Windows Terminal) and navigate to the `docker` folder:

```powershell
cd path\to\Thesis-Allargymap-Platform\docker
```

For example, if your project is on the Desktop:
```powershell
cd C:\Users\YourName\Desktop\Thesis-Allargymap-Platform\docker
```

**Step 3 — Start everything**

```powershell
docker compose up --build
```

The first time you run this, it will download the necessary images and build the
backend. This can take 2–5 minutes. You'll see logs scrolling — that's normal.

When you see a line like `Listening at: http://0.0.0.0:5000`, it's ready.

**Step 4 — Open the app**

| What              | URL                           |
|-------------------|-------------------------------|
| The website       | http://localhost:8080         |
| Backend API       | http://localhost:5000/health  |
| MongoDB           | localhost:27017               |

**Step 5 — Stop everything**

Press `Ctrl + C` in the terminal, then run:
```powershell
docker compose down
```

> Your data in MongoDB is preserved between restarts because it's stored in a Docker volume.

---

### Option B — Run Locally (for development)

Use this if you want to edit code and see changes without rebuilding Docker.

**Requirements:**
- Python 3.11 or 3.12 — download from https://www.python.org/downloads/
  (check "Add Python to PATH" during installation)
- MongoDB Community Server — download from https://www.mongodb.com/try/download/community
  Install it and make sure the MongoDB service is running (it starts automatically on Windows)

**Step 1 — Set up the backend**

Open PowerShell and run these commands one by one:

```powershell
# Go to the backend folder
cd path\to\Thesis-Allargymap-Platform\backend

# Create a virtual environment (an isolated Python workspace)
python -m venv .venv

# Activate it (you need to do this every time you open a new terminal)
.venv\Scripts\activate

# Install the required packages
pip install -r requirements.txt

# Create your local config file (copy the example)
copy .env.example .env
# Then edit .env and set GOOGLE_POLLEN_API_KEY (get one at
# https://console.cloud.google.com/apis/library/pollen.googleapis.com).
# Leave it blank to run on Open-Meteo pollen data only.
```

**Step 2 — Start the backend server**

```powershell
python run.py
```

You should see:
```
 * Running on http://0.0.0.0:5000
 * Debug mode: on
```

**Step 3 — Open the frontend**

Open a second PowerShell window and run:

```powershell
cd path\to\Thesis-Allargymap-Platform\frontend
python -m http.server 8080
```

Then open http://localhost:8080 in your browser.

---

## Loading Real Environmental Data

The `data_collection` folder fetches weather, air quality, and pollen data for
10 Greek cities and imports it into MongoDB. Pollen uses a **hybrid source**:
[Google's Pollen API](https://developers.google.com/maps/documentation/pollen)
is primary (daily 0-5 Universal Pollen Index per plant), and
[Open-Meteo](https://open-meteo.com/) is the fallback + historical/archive
source (hourly grains/m3, no API key needed) — see
`data_collection/README.md` for the full selection rule and why both values
are kept side by side instead of converted.

**Run it:**

```powershell
cd path\to\Thesis-Allargymap-Platform\data_collection

# Install dependencies (only needed once)
pip install -r requirements.txt

# Weather + air quality + Open-Meteo pollen, forecast, all Greek cities
python open_meteo_fetcher.py --mode forecast

# Or fetch the last 30 days of data
python open_meteo_fetcher.py --mode past --days 30

# Or fetch a specific city
python open_meteo_fetcher.py --city Athens --mode forecast

# Google Pollen UPI forecast (needs GOOGLE_POLLEN_API_KEY; 5-day cap)
python google_pollen_fetcher.py

# Import into MongoDB (Google pollen primary by default, auto-falls back to
# Open-Meteo for out-of-window/historical dates or if the key is unset)
python mongo_importer.py
# Or fetch + import in one step:
python open_meteo_fetcher.py --mode forecast --push-to-mongo
```

Output files (CSV + JSON) are saved to `data_collection/output/`.

---

## API Endpoints

Once the backend is running, you can test these in your browser or with a tool like Postman:

| Method | URL                              | What it does                                  |
|--------|----------------------------------|-----------------------------------------------|
| GET    | `/health`                        | Check if the server is running                |
| POST   | `/api/reports/`                  | Submit a symptom report                       |
| GET    | `/api/reports/`                  | List all reports (add `?city=Athens&limit=50`)|
| GET    | `/api/reports/heatmap`           | GeoJSON of report locations/severity (not currently used by the frontend map -- see below) |
| GET    | `/api/env/latest`                | Latest environmental data for each city       |
| GET    | `/api/env/city/Athens`           | Time series for Athens (`?days=7`, or `?start_date=&end_date=YYYY-MM-DD`) |
| GET    | `/api/env/capabilities`          | Provider date-range limits (for UI date pickers) |
| GET    | `/api/predictions/Athens`        | Latest stored AI forecast (`?start_date=&end_date=YYYY-MM-DD` optional) |

**Example — submit a test report:**
```powershell
curl -X POST http://localhost:5000/api/reports/ `
  -H "Content-Type: application/json" `
  -d '{"user_id":"test1","lat":37.98,"lon":23.73,"symptoms":{"rhinitis":7,"asthma":3},"city":"Athens"}'
```

---

## Tech Stack

| Layer        | Technology                                      |
|--------------|-------------------------------------------------|
| Backend      | Python 3.12, Flask 3, Flask-PyMongo             |
| Database     | MongoDB 7 (with 2dsphere geo indexes)           |
| Frontend     | Vanilla JS, Leaflet.js, Chart.js                |
| Container    | Docker Compose (Flask + MongoDB + nginx)        |
| Data source  | Google Pollen API (primary pollen) + Open-Meteo (weather + air quality + fallback/historical pollen) |

---

## Troubleshooting

**"Docker is not recognized"** → Docker Desktop is not running. Open it from the Start menu first.

**"Port 5000 is already in use"** → Something else is using that port. In `docker-compose.yml`,
change `"5000:5000"` to `"5001:5000"` and access the backend at http://localhost:5001.

**"ModuleNotFoundError"** → Your virtual environment isn't activated. Run `.venv\Scripts\activate` again.

**MongoDB won't connect** → If running locally, make sure the MongoDB service is running.
Open Services (Win+R → `services.msc`) and check that "MongoDB" is started.
