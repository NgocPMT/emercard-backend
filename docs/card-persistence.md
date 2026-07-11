# Card Persistence, Provisioning, and Custody

This document describes the backend card foundation and administrator provisioning boundary. It does not add a frontend, direct NFC/QR writing, user activation controls, anonymous lookup, token migration, shipping, or a full audit platform.

## Domain boundary

A physical card owns its public-access identity. The medical profile remains user-owned:

```text
user
  -> one medical profile

user
  -> zero or more cards
       -> stable serial
       -> unique token hash when provisioned
       -> independent lifecycle
```

Multiple current and active cards per user are valid. `is_current` does not enforce a preferred card or a one-card limit.

## Card identity and document

- `serial`: system-generated `EMC-XXXX-XXXX-XXXX-C`, canonical uppercase with checksum.
- Raw public token: 32 secure random bytes encoded as unpadded Base64URL; never persisted.
- `token_hash`: `v1$` plus lowercase SHA-256 hex; persisted only after link provisioning.
- `token_revision`: starts at `0` for a blank card and increments for each provisioning/reprovisioning.
- `owner_id`: BSON user ID or `null`.

Card documents contain:

```text
_id, serial, owner_id, token_hash, token_revision, status, is_current,
provisioned_at, encoding_verified_at, encoded_by_admin_id,
assigned_at, activated_at, disabled_at, lost_at, replaced_at,
issued_at, issued_by_admin_id, voided_at,
replaces_card_id, replacement_card_id, created_at, updated_at
```

Encoding state is derived, never stored as a separate enum:

| Condition | State |
|---|---|
| `token_hash == null` | `not_provisioned` |
| hash exists and `encoding_verified_at == null` | `link_generated` |
| `encoding_verified_at` exists | `verified` |

## Admin workflow

1. `POST /api/v1/admin/cards` creates a serial-only blank card. It requires `Idempotency-Key` and returns safe metadata.
2. `POST /api/v1/admin/cards/{cardId}/provision-link` generates and hashes a token, persists the hash, and returns the raw token/URL exactly once with `Cache-Control: no-store`.
3. The administrator writes the URL with an NFC/QR tool and reads it back.
4. `POST /api/v1/admin/cards/{cardId}/confirm-encoding` validates the exact configured base URL and compares the read-back token hash. The raw token is not returned.
5. A verified card can be assigned to a normal user, reassigned or unassigned before issuance, then issued.
6. A damaged or unusable never-issued card can be voided. Void cards are terminal and cannot be assigned, issued, or activated.

Pre-verification `POST .../reprovision-link` replaces the hash, increments the revision, returns a new URL once, and invalidates the previous URL. Reprovisioning after verification is forbidden.

Assignment never changes the physical link. Assignment does not require profile readiness and does not activate the card. Issuance records handover with `issued_at` and permanently closes direct reassignment, unassignment, and reprovisioning.

## Lifecycle

| Status | Owner | `is_current` | Terminal |
|---|---:|---:|---:|
| `unassigned` | none | false | no |
| `assigned` | required | true | no |
| `active` | required | true | no |
| `disabled` | required | true | no |
| `lost` | required | false | yes |
| `replaced` | required | false | yes |
| `void` | none after retirement | false | yes |

Admin custody transitions are guarded by atomic MongoDB predicates. Lost/replaced lifecycle behavior and the existing replacement transaction remain supported.

## Custody history and idempotency

Administrative ownership operations append events to `card_custody_events`:

```text
card_id, event_type, previous_owner_id, new_owner_id,
performed_by_admin_id, reason, created_at
```

Event types are `assigned`, `reassigned`, `unassigned`, `issued`, and `voided`. Events never contain raw tokens, hashes, URLs, or medical data. Card mutation plus event insertion uses the repository transaction wrapper; transaction verification requires a replica-set MongoDB.

Blank-card creation stores its operation key and resulting card ID in `idempotency_keys`. Repeating the same key returns the original safe card result. Idempotency records currently have no TTL; retention and request-fingerprint policy remain operational follow-up decisions.

Custody-event history is persisted for traceability but is not returned by the current safe card-detail response.

## Indexes

The `cards` collection initializes:

- unique `serial`;
- partial unique `token_hash` for string hashes, allowing multiple blank `null` values;
- `token_revision`;
- `owner_id`, `status`, `(owner_id, is_current)`, `(owner_id, status)`;
- `replaces_card_id`, `replacement_card_id`;
- `(encoding_verified_at, provisioned_at)` for inventory filtering.

`card_custody_events` is indexed by `(card_id, created_at)` and `(previous_owner_id, created_at)`. `idempotency_keys.operation_key` is unique. There is no unique index limiting an owner to one current or active card.

The legacy `medical_profiles.public_access` field and index remain unchanged for compatibility.

## Security and output rules

Every admin route requires the existing `require_admin` dependency. Safe card responses may include ID, serial, status, current flag, derived encoding state, revision, safe owner summary, and operational timestamps. They never include raw tokens, token hashes, public URLs, medical data, cookies, authorization headers, or database details.

Provisioning request bodies and generated URLs must not be logged. Generic request logging records only request ID, method, route, status, and duration.

## Deferred consumers

The following remain out of scope: admin frontend, NFC writer integration, QR rendering, user activation/disablement routes, anonymous emergency lookup, lost/replacement HTTP workflows, shipping, payments, batches, scan history, full audit UI, and legacy profile-token migration.

## Verification

From `emercard-backend/`:

```bash
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run pytest
EMERCARD_TEST_MONGODB_URI=<disposable-replica-set-uri> uv run pytest -m mongo
uv run python -m emercard.db.initialize
uv run python -m emercard.db.initialize
```

The Mongo suite requires a disposable database. Card custody-event transaction coverage requires a replica-set-capable MongoDB.
