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

The initialization command is idempotent and never drops or rebuilds data. Run it twice during local verification. It initializes users, profiles, public links, card assignments, cards, custody-event, and idempotency indexes. Set `EMERCARD_MONGODB_INDEX_INITIALIZATION_MODE=startup` only when deployment should ensure indexes during application startup; incompatible existing index options must fail visibly.

## MongoDB integration

For the isolated real-MongoDB repository suite, provide a disposable replica-set test URI. It never defaults to the local development or Atlas demo database:

```bash
EMERCARD_TEST_MONGODB_URI=<disposable-replica-set-uri> uv run pytest -m mongo
```

Replica-set-capable MongoDB is required for transaction coverage. The current Mongo suite includes coverage for:

- public-link lifecycle and privacy headers
- card-link assignment conflicts and history
- emergency/public lookup through active profile links and current assignments
- legacy migration normalization
- legacy access-field retirement
- card replacement rollback behavior

## Public link operator checks

These commands should also succeed during backend verification:

```bash
uv run python -m emercard.db.public_profile_links generate --profile-id <id>
uv run python -m emercard.db.public_profile_links regenerate --profile-id <id>
uv run python -m emercard.db.public_profile_links disable --profile-id <id>
uv run python -m emercard.db.normalize_legacy_links
uv run python -m emercard.db.retire_legacy_access_fields
```

The first command group should return safe JSON and never print raw tokens in error paths. The migration commands should support dry-run mode and refuse unsafe completion when legacy hashes are shared or retirement prerequisites are missing.

## Card and lookup checks

- active/current/issued/encoding-verified cards resolve through the constrained `profile -> link -> card` path;
- pending/unbound links never resolve publicly; only links with an active current card assignment can resolve;
- disabled, lost, replaced, void, non-current, assigned, unknown, malformed, ownerless, and missing-profile cases return neutral errors;
- multiple active cards for one profile resolve independently;
- disabling a link blocks the unchanged URL, and reactivation restores it when allowed without changing the card assignment or token;
- pre-delivery rebinding revokes the previous link, while delivered/lost/replaced cards retain their historical assignment and revoke only link access;
- direct assignment, card-local provision/reprovision, and detach routes are absent from the generated HTTP contract;
- success and error responses contain the required privacy headers;
- request logs contain only the route template and low-cardinality outcome, never token-bearing paths or hashes.

See [`card-persistence.md`](card-persistence.md) for lifecycle gates, assignment rules, custody history, and the public-link/card relationship.