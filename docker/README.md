# docker

`docker-compose.yml` runs the full AllergyMap stack: MongoDB, the Flask
backend, a one-shot data seeder, a continuously running data collector, and
an nginx-served frontend.

## Services

| Service    | Image / build              | Port         | Purpose |
|------------|------------------------------|--------------|---------|
| `mongo`     | `mongo:7`                    | 27017        | Database; runs `mongo-init/init.js` on first start (creates collections + indexes). |
| `backend`    | `Dockerfile.backend`         | 5000         | Flask API (gunicorn). |
| `seeder`     | `Dockerfile.seeder`          | —            | One-shot: `open_meteo_fetcher.py --mode past --days 30 --push-to-mongo` (`restart: "no"`). |
| `collector`  | `Dockerfile.collector`       | —            | Long-running: `scheduler.py` on the schedule in `data_collection/collector_config.json` (`restart: unless-stopped`). Writes to the `collector_output` volume. |
| `frontend`    | `nginx:alpine`               | 8080         | Serves `../frontend` as static files. |

## Environment

Copy the committed template — Docker Compose automatically loads `.env` for
`${VAR}` substitution, and `.env` itself is gitignored:

```bash
cp .env.example .env
```

The defaults work as-is; no API key is required to bring the stack up.

`GOOGLE_POLLEN_API_KEY` is passed through to the `backend`, `seeder`, and
`collector` containers (`${GOOGLE_POLLEN_API_KEY:-}` — empty by default, which
makes them fall back to Open-Meteo-only pollen automatically; see
`data_collection/README.md`).

## Usage

```bash
cd docker
cp .env.example .env
docker compose up --build
```

Then load the historical dataset once (see [The collector](#the-collector)):

```bash
docker compose run --rm collector python scheduler.py --backfill 92
```

| What        | URL                          |
|-------------|-------------------------------|
| Frontend    | http://localhost:8080         |
| Backend API | http://localhost:5000/health  |
| MongoDB     | localhost:27017               |

### The collector

`collector` starts with the stack and then sleeps until its next scheduled run,
so nothing appears in its log between cycles. Useful commands:

```bash
docker compose logs -f collector          # watch it work
docker compose run --rm collector python scheduler.py --backfill 92
docker compose run --rm collector python scheduler.py --once
docker compose stop collector             # pause collection
```

Run the 92-day backfill once when the stack is first brought up: it is the
widest retroactive window Open-Meteo serves for pollen and dust, and it gives
the correlation and evaluation work a real dataset immediately.
