# EmerCard Backend

Phase 1 provides the backend and database foundation for the fictional EmerCard school-project demo. It is not production-safe for real medical information.

## Requirements

- Python 3.14.6
- [`uv`](https://docs.astral.sh/uv/)
- Docker Engine with Compose v2

## Local setup

```bash
python3.14 -m venv .venv
source .venv/bin/activate
uv sync --all-groups
cp .env.example .env
```

Dependencies must be installed while the project virtual environment is active. `uv.lock` is committed and is the source of reproducible dependency resolution.

## Run modes

For a host-run API, start MongoDB through Compose and use the localhost URI from `.env`:

```bash
docker compose up -d mongodb
source .venv/bin/activate
uv run uvicorn emercard.main:app --host 127.0.0.1 --port 8000
```

For the full container path, the API must use the Compose service name (`mongodb`) rather than `localhost`:

```bash
docker compose up --build
```

The API listens on `http://localhost:8000`; MongoDB is published on `localhost:27017` for host-run development. MongoDB data persists in the named `mongodb-data` volume. The Compose file uses MongoDB 8.2 as the verified local fallback because MongoDB 8.3 exits on Linux kernel 6.19+ (SERVER-121912); revisit the Plan 01 8.3 baseline when the runtime becomes compatible.

## Documentation

Backend contracts and operational documentation:

- [`docs/http-contract.md`](docs/http-contract.md): API and transport behavior.
- [`docs/configuration.md`](docs/configuration.md): environment and persistence configuration.
- [`docs/verification.md`](docs/verification.md): quality, database, and smoke verification.
- [`docs/maintenance.md`](docs/maintenance.md): trusted maintenance commands.
- [`docs/database-models.md`](docs/database-models.md): persistence contracts.
- [`docs/card-persistence.md`](docs/card-persistence.md): card identity and lifecycle design.
- [`docs/smoke-checks.md`](docs/smoke-checks.md): local and Compose health/readiness checks.
- [`docs/deployment.md`](docs/deployment.md): Render and MongoDB Atlas deployment boundary.
