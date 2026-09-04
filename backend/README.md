# backend

Flask REST API for AllergyMap (symptom reports, environmental data, ML predictions).

## Structure

```
backend/
├── run.py                 ← python run.py  (dev server, port 5000)
├── requirements.txt
├── .env.example            ← copy to .env before running
└── app/
    ├── __init__.py         ← app factory, registers all blueprints
    ├── config.py            ← env-var driven config (loads backend/.env via python-dotenv)
    ├── extensions.py        ← MongoDB (flask-pymongo) connection
    ├── models/               ← document schema docstrings (not ORM models)
    │   ├── report.py         ← symptom report + build_report()
    │   ├── user.py           ← anonymous participant (device) + build_upsert()
    │   └── env_snapshot.py    ← weather/pollen/air_quality snapshot schema
    ├── routes/
    │   ├── health.py         ← GET /health
    │   ├── users.py          ← POST/GET/DELETE /api/users/me (anonymous identity)
    │   ├── reports.py        ← POST/GET /api/reports, GET /api/reports/heatmap
    │   ├── env_data.py       ← GET /api/env/latest, /city/<city> (?days= or ?start_date=&end_date=), /capabilities
    │   └── predictions.py    ← GET/POST /api/predictions/* (?start_date=&end_date= supported too; lazily imports data_collection/predictor.py)
    └── utils/
        ├── geo.py             ← haversine distance, nearest_city()
        ├── identity.py         ← X-Device-Id validation + @require_device_id
        └── validation.py       ← parse_date_range() -- shared start_date/end_date validation for env_data.py and predictions.py
```

## Configuration

All config comes from environment variables (never hard-coded), loaded from
`backend/.env` via `python-dotenv` in `config.py`:

| Variable                | Default                                    | Notes |
|--------------------------|---------------------------------------------|-------|
| `FLASK_ENV`               | `development`                               | selects `DevelopmentConfig` / `ProductionConfig` |
| `SECRET_KEY`               | `dev-secret-key`                            | change in production |
| `MONGO_URI`                | `mongodb://localhost:27017/allergymap`      | |
| `GOOGLE_POLLEN_API_KEY`    | `""` (empty)                                | primary pollen source; empty = Open-Meteo-only fallback everywhere. See `data_collection/README.md` for the full hybrid-source explanation. |

```bash
cp .env.example .env   # then fill in real values; .env is gitignored
```

## Running

```bash
pip install -r requirements.txt
python run.py                 # http://localhost:5000
```

## Participant identity

AllergyMap has **no accounts**: no e-mail address, no password, no personal
data of any kind. A participant is a *device*, identified by a random UUID v4
that the browser generates on first use and keeps in `localStorage`
(`frontend/js/identity.js`). It travels in the `X-Device-Id` request header
and is validated by `app/utils/identity.py` before any identity-bearing route
runs.

Why this rather than login:

- The platform's purpose is crowdsensing. What the correlation engine and the
  alerting engine need is *"the same participant over time"*, which an opaque
  identifier gives; **who** that participant is adds nothing to the analysis.
- With no personal data stored, the GDPR surface shrinks to almost nothing --
  the data is pseudonymous, and erasure is a single call
  (`DELETE /api/users/me`) rather than an account-deletion workflow.
- Participation costs one page load. A registration form measurably suppresses
  contribution in crowdsensing systems, and the dataset is the thesis result.

The trade-off, stated plainly: **the identifier is a bearer credential**.
Anyone holding it can read and write that profile, and there is no password to
prove otherwise. That is acceptable for pseudonymous symptom data and it is
what makes the profile portable -- the UI shows the identifier as a *transfer
code*, and typing it on a second device adopts the same profile. It would not
be acceptable for anything clinically identifying, and the paper says so.

Validation is not cosmetic: only canonical UUID v4 is accepted. Rejecting
client-chosen strings keeps identifiers unguessable (122 bits of entropy) and
stops personal data from leaking into the database through the identity field
itself. UUID v1 is rejected too -- it embeds a MAC address and a timestamp.

| Endpoint                 | Purpose |
|---------------------------|---------|
| `POST /api/users/me`       | Register this device, or refresh `last_seen_at`. Idempotent; optional `{"alias": "phone"}` body. |
| `GET /api/users/me`        | Read this device's record. 404 means "never registered", which is the normal answer after storage is cleared. |
| `DELETE /api/users/me`     | Erase the participant: identity record and allergy profile are deleted; symptom reports are **anonymised** (`user_id` unset) so the observations stay in the dataset. `?delete_reports=true` deletes them instead. |

`POST /api/reports` requires the same header and attributes the report to the
calling device. A `user_id` in the request body is ignored -- otherwise any
client could submit reports as another participant.

There is deliberately no route that takes a device id in the path or query
string: a bearer credential does not belong in access logs, browser history or
`Referer` headers. The API can only speak about "me".

The `users` collection has a **unique** index on `device_id`
(`docker/mongo-init/init.js`), which is what makes the register-or-touch
upsert idempotent under concurrent requests. `mongo-init` only runs on a first
container start, so on an existing volume create it by hand:

```bash
docker compose exec mongo mongosh allergymap --quiet --eval \
  'db.users.createIndex({device_id:1},{unique:true}); db.allergy_profiles.createIndex({device_id:1},{unique:true})'
```


## Custom date ranges

`GET /api/env/city/<city>` and `GET /api/predictions/<city>[/<variable>]` accept
either a relative `?days=N` window (default) or an explicit
`?start_date=YYYY-MM-DD&end_date=YYYY-MM-DD` range. Both ends are validated
(`app/utils/validation.py`) and return HTTP 400 with a message on malformed
or inverted ranges. `GET /api/env/capabilities` returns each provider's actual
date-range limits (Google Pollen: ~5-day forecast only; Open-Meteo: 7-day
forecast / 92-day past / full weather archive) so a frontend date picker can
clamp itself instead of hard-coding those numbers.

## Testing

```bash
python -m unittest discover -s tests -v
```

Tests cover `parse_date_range()`, the predictions date-range filter, the
timestamp serialisation helpers, the device-identity validation and the
`users` document builders (pure functions plus the `@require_device_id`
decorator against a throwaway Flask app -- no MongoDB required). Route-level
integration testing needs a running MongoDB and is not currently automated.

## Note on the `predictions` blueprint

The brief architecture for this project describes a separate `ml_service/`
Flask app (port 5002) and an `analysis.py` route module. The actual v1
codebase instead ships ML forecasting as `data_collection/predictor.py`,
invoked in-process by `routes/predictions.py` (`POST /api/predictions/run`).
v2 development adapts to this existing structure rather than introducing a
second Flask service, to avoid duplicating the RandomForest logic.

## Timestamps

All datetimes leaving the API carry an explicit `+00:00` offset, rendered by
`app/utils/serialization.py` (`iso_utc`, `serialize_doc`). MongoDB stores BSON
dates as UTC and pymongo returns them naive; emitting `.isoformat()` directly
would produce an offset-less string, which browsers parse as **local** time --
a silent two to three hour error for a Greek deployment. Use `serialize_doc`
for any new route that returns documents.
