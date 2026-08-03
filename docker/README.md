# docker

`docker-compose.yml` runs the full AllergyMap stack: MongoDB, the Flask
backend, a one-shot data seeder, and an nginx-served frontend.

## Services

| Service    | Image / build              | Port         | Purpose |
|------------|------------------------------|--------------|---------|
| `mongo`     | `mongo:7`                    | 27017        | Database; runs `mongo-init/init.js` on first start (creates collections + indexes). |
| `backend`    | `Dockerfile.backend`         | 5000         | Flask API (gunicorn). |
| `seeder`     | `Dockerfile.seeder`          | —            | One-shot: `open_meteo_fetcher.py --mode past --days 30 --push-to-mongo` (`restart: "no"`). |
| `frontend`    | `nginx:alpine`               | 8080         | Serves `../frontend` as static files. |

## Environment

Create `docker/.env` (gitignored) alongside this compose file — Docker Compose
automatically loads it for `${VAR}` substitution:

```env
SECRET_KEY=change-me
GOOGLE_POLLEN_API_KEY=your-google-pollen-api-key-here
```

`GOOGLE_POLLEN_API_KEY` is passed through to both the `backend` and `seeder`
containers (`${GOOGLE_POLLEN_API_KEY:-}` — empty by default, which makes both
services fall back to Open-Meteo-only pollen automatically; see
`data_collection/README.md`).

## Usage

```bash
cd docker
docker compose up --build
```

| What        | URL                          |
|-------------|-------------------------------|
| Frontend    | http://localhost:8080         |
| Backend API | http://localhost:5000/health  |
| MongoDB     | localhost:27017               |
