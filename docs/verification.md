# Verification Contract

Run these commands from `emercard-backend/` with the virtual environment activated:

```bash
source .venv/bin/activate
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run pytest
```

## Database indexes

Initialize the Phase 1 collections and indexes explicitly against the configured database:

```bash
uv run python -m emercard.db.initialize
```

The initialization command is idempotent and never drops or rebuilds data. Run it twice during local verification. Set `EMERCARD_MONGODB_INDEX_INITIALIZATION_MODE=startup` only when deployment should ensure indexes during application startup; incompatible existing index options must fail visibly.

## MongoDB integration

For the isolated real-MongoDB repository suite, provide a disposable replica-set test URI. It never defaults to the local development or Atlas demo database:

```bash
EMERCARD_TEST_MONGODB_URI=<disposable-replica-set-uri> uv run pytest -m mongo
```

Replacement transaction coverage requires a replica-set-capable MongoDB. Card-specific invariants are documented in [`card-persistence.md`](card-persistence.md).

## Smoke checks

For health and readiness checks, follow [`smoke-checks.md`](smoke-checks.md). `/health` is a process liveness check and remains successful if MongoDB is stopped; `/ready` reports database readiness and returns `503` with a safe error envelope when MongoDB is unavailable. `X-Request-ID` is generated or safely propagated on responses.
