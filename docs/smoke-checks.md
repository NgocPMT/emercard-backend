# Plan 01 smoke checks

Run these checks from `emercard-backend/` with the virtual environment activated. Compose uses MongoDB 8.2 locally because MongoDB 8.3 is incompatible with the current Linux 6.19+ kernel; revisit this fallback when the runtime changes.

## Host-run API

```bash
source .venv/bin/activate
cp .env.example .env
docker compose up -d mongodb
uv run uvicorn emercard.main:app --host 127.0.0.1 --port 8000
```

In another shell:

```bash
curl -i http://localhost:8000/health
curl -i http://localhost:8000/ready
```

Stop MongoDB and repeat both calls. `/health` must remain `200`; `/ready` must become `503` without exposing the URI or driver error.

## Full Compose

```bash
docker compose up --build
curl -i http://localhost:8000/health
curl -i http://localhost:8000/ready
```

The container API uses `mongodb://mongodb:27017`; a host-run API uses `mongodb://localhost:27017`. Do not interchange these values.
