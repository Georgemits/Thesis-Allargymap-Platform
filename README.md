# AllergyMap Platform

A crowdsensing web platform for monitoring allergic diseases and their correlation
with environmental factors (pollen, air quality, weather) in Greece.

Built as part of a university thesis. Users report their allergy symptoms through
a simple form, which gets combined with real environmental data (pollen, humidity,
dust, temperature) to produce live heatmaps and dashboards.

---

## Quick Start for Reviewers

Everything runs in Docker. On a machine with Docker Desktop installed:

```bash
git clone https://github.com/Georgemits/Thesis-Allargymap-Platform.git
cd Thesis-Allargymap-Platform/docker
cp .env.example .env          # defaults are fine, no API key needed
docker compose up --build
```

| What | URL |
|------|-----|
| Frontend (map, report form, dashboard) | http://localhost:8080 |
| Backend API health check | http://localhost:5000/health |

**Loading the data.** The stack starts with an empty database and the `seeder`
service fills in the last 30 days automatically. To load the full historical
dataset this thesis reports on, run the backfill once after the stack is up:

```bash
docker compose run --rm collector python scheduler.py --backfill 92
```

It takes a few minutes and needs internet access. 92 days is the widest
retroactive window Open-Meteo serves for pollen and dust. From then on the
`collector` service keeps `env_snapshots` current on the schedule in
`data_collection/collector_config.json`.

**API keys are optional.** With `GOOGLE_POLLEN_API_KEY` left empty, pollen comes
from Open-Meteo and the platform is fully functional. Supplying a Google Maps
Platform key with the Pollen API enabled promotes Google to the primary pollen
source, with Open-Meteo as the fallback and historical backfill provider.

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
│       │   ├── users.py       ← POST/GET/DELETE /api/users/me (anonymous identity)
│       │   ├── correlations.py ← GET /api/correlations   (symptoms vs environment)
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

---

## Deploying it publicly

The stack runs as **one web service plus a hosted database**. Flask serves the
frontend and the API from the same origin (`backend/app/routes/site.py`), so a
deployment is a single container — and the browser never makes a cross-origin
request, which takes CORS out of the deployed system entirely. Locally, nginx
reproduces that shape by proxying `/api` to the backend container, so
development and production behave the same instead of differing in a way that
only shows up after deploying.

`render.yaml` describes the service for [Render](https://render.com); any host
that runs a Dockerfile will do. The database is **MongoDB Atlas** rather than a
Mongo container, for one reason: a free web service has no persistent disk, so
a database inside the container would be wiped on every deploy, taking the
collected environmental series with it.

**Steps**

1. Create a free MongoDB Atlas cluster (M0, 512 MB — the current dataset is
   about 25 MB). Add a database user, and allow network access from the
   service. A free Render service has no fixed outbound IP, so that means
   `0.0.0.0/0`; the connection string is then the only credential, which is why
   it must never be committed and should be rotated if it leaks.
2. Create a Blueprint on Render pointing at this repository. It reads
   `render.yaml` and builds `docker/Dockerfile.backend`.
3. Set the two secrets in the Render dashboard, not in the repo:
   `MONGO_URI` (the Atlas connection string) and `GOOGLE_POLLEN_API_KEY`.
   `SECRET_KEY` is generated by Render.
4. Create the indexes once against the Atlas database — `mongo-init/init.js`
   only runs for a local Mongo container:
   ```bash
   mongosh "<your Atlas connection string>" --eval '
     db.reports.createIndex({location: "2dsphere"});
     db.reports.createIndex({timestamp: -1});
     db.env_snapshots.createIndex({city: 1, timestamp: -1});
     db.users.createIndex({device_id: 1}, {unique: true});
     db.users.createIndex({username: 1}, {unique: true, sparse: true});
     db.allergy_profiles.createIndex({device_id: 1}, {unique: true});'
   ```
5. Load the environmental history into Atlas by pointing the collector's
   `MONGO_URI` at it and running the backfill described above.

**What the free tier costs you.** A free Render service sleeps after roughly
15 minutes of inactivity, so the first visit after a quiet period waits half a
minute for the container to wake. That is fine for collecting reports and
awkward during a live demonstration — worth keeping a browser tab warm before
presenting, or moving to a paid instance for the day.

**The collector does not run on the free tier** (scheduled jobs are a paid
feature there). Until that is arranged it keeps running wherever it runs today
— a laptop with `docker compose up collector` and `MONGO_URI` pointed at Atlas
does the job, and a scheduled GitHub Actions workflow is the free alternative.


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

### Keeping it collecting automatically

The commands above are one-off fetches. For a continuous record, use the
scheduled collector instead:

```bash
cd data_collection

# One-off historical load: 92 days is Open-Meteo's retroactive limit for
# pollen and dust. Run this once, before anything else.
python scheduler.py --backfill 92

# The weather endpoint only reaches back ~70 days, so the oldest weeks come
# back with null temperature and humidity. Close that gap from the archive:
python scheduler.py --fill-weather 2026-05-26 2026-06-19

# Then let it run on a schedule (twice daily by default)
python scheduler.py
```

Under Docker this is the `collector` service and starts with the stack —
nothing extra to run. Google's Pollen API is forecast-only with no historical
endpoint, so its daily UPI values exist only if something captured them on the
day; that is the collector's main job. Configuration lives in
`data_collection/collector_config.json`; see `data_collection/README.md`.

---

## API Endpoints

Once the backend is running, you can test these in your browser or with a tool like Postman:

| Method | URL                              | What it does                                  |
|--------|----------------------------------|-----------------------------------------------|
| GET    | `/health`                        | Check if the server is running                |
| POST   | `/api/auth/register`             | Create an account for this device (username + password) |
| POST   | `/api/auth/login`                | Sign in; returns the device id to use from then on |
| GET    | `/api/auth/status`               | Does this device have an account?             |
| GET    | `/api/profiles/allergens`        | The allergens a participant can declare + the severity scale |
| GET    | `/api/profiles/me`               | This participant's allergy profile            |
| PUT    | `/api/profiles/me`               | Create or replace it                          |
| DELETE | `/api/profiles/me`               | Delete it, keeping the participant            |
| POST   | `/api/users/me`                  | Register this device (anonymous), or refresh its last-seen time |
| GET    | `/api/users/me`                  | Read this device's record                     |
| DELETE | `/api/users/me`                  | Erase this participant (`?delete_reports=true` to drop the reports too) |
| POST   | `/api/reports/`                  | Submit a symptom report                       |
| GET    | `/api/reports/`                  | List all reports (add `?city=Athens&limit=50`)|
| GET    | `/api/reports/heatmap`           | GeoJSON of report locations/severity (not currently used by the frontend map -- see below) |
| GET    | `/api/env/latest`                | Latest environmental data for each city       |
| GET    | `/api/env/city/Athens`           | Time series for Athens (`?days=7`, or `?start_date=&end_date=YYYY-MM-DD`) |
| GET    | `/api/env/capabilities`          | Provider date-range limits (for UI date pickers) |
| GET    | `/api/predictions/Athens`        | Latest stored AI forecast (`?start_date=&end_date=YYYY-MM-DD` optional) |
| GET    | `/api/correlations`              | Symptoms vs environment: every variable against one symptom |
| GET    | `/api/correlations/lags`         | The same analysis at 0/3/6/12/24-hour exposure lags |
| GET    | `/api/correlations/combinations` | Mean severity per allergen band × temperature or humidity band |
| GET    | `/api/correlations/me`           | Restricted to the allergens this participant declared |

The three `/api/users/me` routes and `POST /api/reports/` identify the caller
from an `X-Device-Id` header holding a UUID v4 — there is no login, and the
header is the only credential. See **Participant identity** below.

**Example — submit a test report:**
```powershell
curl -X POST http://localhost:5000/api/reports/ `
  -H "Content-Type: application/json" `
  -H "X-Device-Id: 3f2a9c1e-7b4d-4a6f-9e21-0c8d5b1a2f30" `
  -d '{"lat":37.98,"lon":23.73,"symptoms":{"sneezing":7,"cough":3},"city":"Athens"}'
```

---

## Participant identity

The platform stores **no accounts and no personal data**. A participant is a
device, identified by a random UUID v4 the browser generates on first visit
and keeps in `localStorage`; it is sent in the `X-Device-Id` header on the
requests that concern that participant's own data, and nowhere else.

This is a deliberate design decision, not a shortcut. Crowdsensing needs
*"the same participant over time"* — which an opaque identifier provides —
and not *who* the participant is; holding no personal data keeps the dataset
pseudonymous, makes erasure a single API call, and removes the registration
step that suppresses participation. The cost is that the identifier is a
bearer credential with no password behind it, which is also what makes the
profile portable: the UI shows it as a **transfer code**, and typing it on a
second device adopts the same profile.

**Accounts are optional and sit on top of this.** Creating one (username and
password — no e-mail address) attaches it to the identity the browser already
has, so nothing contributed anonymously is lost, and signing in on another
device hands that device the same identifier. It is a way to *recover* an
identity, not a second notion of who someone is: the device id still authorises
every request. There is no mail server behind the platform, so a forgotten
password cannot be reset — which is exactly why the transfer code still exists.

`backend/README.md` documents the endpoints, the validation rules and the
erasure semantics; `frontend/README.md` documents the browser side.

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
