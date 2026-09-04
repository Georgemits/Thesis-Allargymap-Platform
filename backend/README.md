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
    │   ├── user.py           ← participant (device, optional account) + build_upsert()
    │   ├── allergy_profile.py ← allergen catalogue + severity validation
    │   └── env_snapshot.py    ← weather/pollen/air_quality snapshot schema
    ├── routes/
    │   ├── health.py         ← GET /health
    │   ├── users.py          ← POST/GET/DELETE /api/users/me (anonymous identity)
    │   ├── auth.py           ← POST /api/auth/register|login, GET /api/auth/status
    │   ├── profiles.py       ← GET/PUT/DELETE /api/profiles/me, GET /api/profiles/allergens
    │   ├── correlations.py   ← GET /api/correlations[/lags|/combinations|/me]
    │   ├── reports.py        ← POST/GET /api/reports, GET /api/reports/heatmap
    │   ├── env_data.py       ← GET /api/env/latest, /city/<city> (?days= or ?start_date=&end_date=), /capabilities
    │   └── predictions.py    ← GET/POST /api/predictions/* (?start_date=&end_date= supported too; lazily imports data_collection/predictor.py)
    ├── services/             ← analysis: no Flask, no MongoDB, directly testable
    │   ├── statistics.py      ← Pearson, Spearman, p-values (no SciPy at runtime)
    │   └── correlation.py     ← report↔snapshot pairing, lags, combination tables
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

### Optional accounts

An account (username + password) sits **on top of** that identity rather than
replacing it. `POST /api/auth/register` attaches a username and a scrypt
password hash to the user document of the device that is calling, so
everything contributed anonymously up to that moment stays with the person.
`POST /api/auth/login` looks the account up and returns its `device_id`, which
the browser then uses as its own identity -- that is what makes a profile
follow someone to a second device or survive clearing their browser. Signing
out is client-side only: the browser forgets the identifier and generates a
fresh anonymous one. There is no server-side session to expire.

No e-mail address is collected, deliberately: it is personal data the platform
has no use for, and a "reset your password" flow would be a promise this
system cannot keep -- there is no mail server behind it. That, plus the absence
of login rate-limiting, is documented in the thesis as a known limitation
rather than hidden. Accounts are optional throughout; the transfer code below
remains the recovery path for participants who do not want one.

`password_hash` never leaves the API: every response that touches the `users`
collection goes through `app.models.user.public_user`, which is a whitelist of
publishable fields rather than a blacklist of secret ones.

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
| `POST /api/auth/register`  | Attach a username + password to the calling device. 400 malformed, 409 taken or already signed in. |
| `POST /api/auth/login`     | Username + password → that account's `device_id`. 401 on any failure, with one message for both cases. |
| `GET /api/auth/status`     | Whether the calling device carries an account. |
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


## Allergy profile

`app/models/allergy_profile.py` holds the catalogue of allergens a participant
can declare, and every entry carries the **exact dotted path into an
`env_snapshots` document** that it is scored against
(`pollen.olive_pollen`, `air_quality.dust`, …). That coupling is the point: an
allergen the platform cannot measure could never produce a risk score or an
alert, so the participant would tick a box and nothing would ever come of it.
House dust mite, pet dander and mould are the notable omissions -- clinically
important, but indoor allergens that no environmental feed here reports.

Severity is an ordinal 0-3 (`not affected` / `mild` / `moderate` / `severe`),
not the 0-10 VAS used for symptoms: an allergy is a stable property of the
person rather than a measurement of a moment, four levels are about as fine as
anyone answers consistently, and the values double as weights in the risk
score. Severity 0 is stored as *absence from the document*, so the correlation
engine can iterate the map directly.

| Endpoint                     | Purpose |
|-------------------------------|---------|
| `GET /api/profiles/allergens` | The catalogue + severity scale. Public — it describes what the platform measures, not a participant. |
| `GET /api/profiles/me`        | This participant's profile; 404 before one is saved, which the page renders as an empty form. |
| `PUT /api/profiles/me`        | Create or replace it. The allergen map is replaced wholesale, because that is the only way un-ticking an allergen can work. |
| `DELETE /api/profiles/me`     | Delete the profile, keeping the participant and their reports. |

Profiles live in their own collection (`allergy_profiles`, unique index on
`device_id`) rather than embedded in the user document, so one can be replaced
or erased without touching the identity record. The collection name is defined
once, in `app/models/allergy_profile.py`, and imported by both the routes that
write profiles and the erasure handler that deletes them.


## Correlation engine

The thesis' central analysis (supervisor TODO #2): do reported symptoms move
with the measured environment? `app/services/` holds it, and holds no Flask and
no MongoDB code -- the routes fetch documents and pass them in, so every claim
the thesis makes about this can be tested without a database.

**The join.** A report carries coordinates and a timestamp; snapshots are
per-city and hourly. Each report is matched to the snapshot for its city
closest in time, and only within `DEFAULT_MATCH_WINDOW_HOURS` (3) -- a report
with nothing near it is dropped rather than paired with whatever exists. The
city comes from the coordinates via `nearest_city`, not from the free-text
`city` field a participant may have mistyped or left blank.

**Exposure lag.** Symptoms follow exposure rather than accompanying it, so
every analysis takes a `lag_hours` and matches against conditions that many
hours *earlier*. `GET /api/correlations/lags` runs the same analysis at 0, 3,
6, 12 and 24 hours and returns all of them: reporting only the strongest lag,
picked after seeing the results, would be selecting the finding.

**The statistics are computed here, from first principles** -- Pearson,
Spearman with tie-averaged ranks, and two-sided p-values through a
hand-written regularized incomplete beta. SciPy is not a runtime dependency;
it is used during development to *check* this code, and
`tests/test_statistics.py` pins the reference values that comparison produced.
Both coefficients are reported because a bounded 0-10 symptom scale flattens at
the top: a real dose-response can be strongly monotonic and only moderately
linear.

**What it refuses to do.** No coefficient is reported from fewer than
`MIN_SAMPLES` (20) pairs -- the result comes back with `insufficient_data: true`
and the sample size, because "r = 0.87 (n = 4)" reads as a finding when it is
noise. Each response also carries `tests_run`: correlating eleven variables
against seven symptoms is dozens of hypotheses, and a p below 0.05 among them
is expected by chance.

**Combinations** (`/combinations`) answer the part of the brief a coefficient
cannot: a 3×3 table of mean severity per allergen band × temperature or
humidity band, which can show that high pollen matters mainly when it is also
hot. Bands are the data's own terciles, with the cut points reported --
inventing a clinical threshold for "high olive pollen in grains/m³" would
decide the result in advance.

| Endpoint | Purpose |
|-----------|---------|
| `GET /api/correlations` | Every variable against one symptom. `?symptom=&days=&city=&lag_hours=` |
| `GET /api/correlations/lags` | The same analysis at each candidate lag. `?variable=` to focus on one |
| `GET /api/correlations/combinations` | Allergen band × modifier band mean severity. `?variable=&modifier=` |
| `GET /api/correlations/me` | Restricted to this participant's declared allergens, returning both their **personal** result and the **population** one — the personal view is empty until they have reported enough, and the population view is what a risk score falls back on |


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
timestamp serialisation helpers, the device-identity validation, the `users`
document builders, username/password validation, the `public_user` whitelist
the allergy-profile validation, the correlation statistics (against
reference values from SciPy) and the correlation engine's joining, lagging and
banding (pure functions plus the `@require_device_id` decorator against a
throwaway Flask app -- no MongoDB required). Route-level integration testing needs a running MongoDB and is not
currently automated.

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
