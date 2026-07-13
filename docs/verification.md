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

The initialization command is idempotent and never drops or rebuilds data. Run it twice during local verification. It initializes users, profiles, cards, custody-event, and idempotency indexes. Set `EMERCARD_MONGODB_INDEX_INITIALIZATION_MODE=startup` only when deployment should ensure indexes during application startup; incompatible existing index options must fail visibly.

## MongoDB integration

For the isolated real-MongoDB repository suite, provide a disposable replica-set test URI. It never defaults to the local development or Atlas demo database:

```bash
EMERCARD_TEST_MONGODB_URI=<disposable-replica-set-uri> uv run pytest -m mongo
```

Replacement and custody-event transaction coverage requires a replica-set-capable MongoDB. Card-specific invariants, admin gates, safe output rules, and manual NFC/QR verification are documented in [`card-persistence.md`](card-persistence.md).

Public profile quick-demo verification should additionally confirm:

- `uv run python -m emercard.db.public_profile_links generate --profile-id <id>` prints a public URL once and never prints a raw token on failure paths;
- `uv run python -m emercard.db.public_profile_links regenerate --profile-id <id>` invalidates the old URL;
- `uv run python -m emercard.db.public_profile_links disable --profile-id <id>` preserves metadata while blocking lookup;
- `uv run pytest tests/test_public_profile_links.py -q` covers the lifecycle, command, log-redaction, and synchronization regressions.

Anonymous emergency lookup verification should additionally confirm:

- active/current/issued/encoding-verified cards resolve through the constrained token-hash query;
- disabled, lost, replaced, void, non-current, assigned, unknown, malformed, ownerless, and missing-profile cases return the neutral 404;
- multiple active cards for one owner resolve independently;
- disablement blocks the unchanged physical URL and reactivation restores it;
- success and error responses contain the required no-store/noindex privacy headers;
- request logs contain only the route template and low-cardinality outcome, never token-bearing paths or hashes.

## Smoke checks

For health and readiness checks, follow [`smoke-checks.md`](smoke-checks.md). `/health` is a process liveness check and remains successful if MongoDB is stopped; `/ready` reports database readiness and returns `503` with a safe error envelope when MongoDB is unavailable. `X-Request-ID` is generated or safely propagated on responses.
