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

## Verification

```bash
source .venv/bin/activate
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run pytest
```

Initialize the Phase 1 collections and indexes explicitly against the configured database:

```bash
uv run python -m emercard.db.initialize
```

The initialization command is idempotent and never drops or rebuilds data. Run it twice during local verification. Set `EMERCARD_MONGODB_INDEX_INITIALIZATION_MODE=startup` only when the deployment should ensure indexes during application startup; incompatible existing index options fail visibly.

For the isolated real-MongoDB repository suite, provide a disposable test URI. It never defaults to the local development or Atlas demo database:

```bash
EMERCARD_TEST_MONGODB_URI=mongodb://localhost:27017 uv run pytest -m mongo
```

Smoke checks:

```bash
curl -i http://localhost:8000/health
curl -i http://localhost:8000/ready
curl -i http://localhost:8000/api/v1/meta
```

`/health` is a process liveness check and remains successful if MongoDB is stopped. `/ready` pings MongoDB through the single managed PyMongo async client and returns `503` with a safe error envelope when MongoDB is unavailable. `X-Request-ID` is generated or safely propagated on responses.

## Configuration

All environment-dependent values are loaded by the typed `Settings` object. Copy `.env.example` to `.env` for local work. Deployed environments must provide values through platform environment settings, not committed files.

For Atlas, set `EMERCARD_MONGODB_URI` to the TLS connection string and use a separate least-privilege demo database user. Set `EMERCARD_ENVIRONMENT=demo`, `EMERCARD_DEBUG=false`, `EMERCARD_MONGODB_TLS_REQUIRED=true`, a random 32-character-or-longer `EMERCARD_AUTH_SECRET`, `EMERCARD_AUTH_COOKIE_SECURE=true`, and the exact deployed frontend origin (`https://app.emercard.id.vn`) in `EMERCARD_CORS_ORIGINS`. Keep `EMERCARD_CORS_ALLOW_CREDENTIALS=true`; authentication uses a host-only HTTP-only cookie and never sets a cookie domain.

Do not log or commit MongoDB URIs, secrets, tokens, cookies, request bodies, or fictional medical fields. Do not use real medical data.

## HTTP contract

- `GET /health` returns `200` and `{ "status": "ok" }` without a database query.
- `GET /ready` returns `200` only after a successful MongoDB ping, otherwise `503` with `error.code=database_unavailable`.
- `GET /api/v1/meta` returns non-sensitive application and build metadata.
- `POST /api/v1/auth/register` returns `201` with a direct `CurrentUserOutput`; registration does not authenticate the account.
- `POST /api/v1/auth/login` returns `200` with `CurrentUserOutput` and sets the short-lived `emercard_session` cookie.
- `GET /api/v1/me` returns the authenticated `CurrentUserOutput`; `POST /api/v1/auth/logout` returns `204` and expires the cookie.
- Browser calls from the exact configured frontend origin must use `fetch(..., { credentials: "include" })`; local development uses `http://localhost:4321`.
- Phase 1 logout is stateless: a copied JWT remains valid until its 15-minute expiry. Do not claim immediate revocation.
- Errors contain an error code, human-readable message, optional sanitized details, and `request_id`.
- CORS uses the configured exact origin allowlist with credentials enabled; wildcard origins and configured cookie domains are rejected by settings.

## Deployment boundary

The intended demo deployment is one Render web service connected to one MongoDB Atlas demo cluster. Configure the Render build command as `uv sync --locked --no-dev`, the start command as `uv run --no-dev uvicorn emercard.main:app --host 0.0.0.0 --port $PORT`, and the health check path as `/health`. Keep Atlas network access and the final frontend URL restricted to the project’s deployment requirements. Record provider-specific service names and URLs in deployment notes only after they are created; never commit credentials.

Phase 1 now includes the `users` and `medical_profiles` persistence contract, typed repositories, cookie-based authentication endpoints, and isolated verification. Profile/public-link HTTP routes, Redis, messaging, encryption, cards, scans, audits, and admin features remain out of scope for this stage.
