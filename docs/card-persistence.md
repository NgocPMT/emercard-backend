# Card Persistence, Link Assignment, and Custody

This document explains how physical cards, public links, and custody history work in the backend. It covers the implemented link-first model: profiles may own many public links, each physical card uses one distinct card-purpose link, and anonymous lookup only resolves through active public-link records.

## Where this lives

- `src/emercard/modules/cards/models.py`
- `src/emercard/modules/cards/repository.py`
- `src/emercard/modules/cards/service.py`
- `src/emercard/modules/public_links/models.py`
- `src/emercard/modules/public_links/repository.py`
- `src/emercard/modules/public_links/service.py`
- `src/emercard/modules/card_link_assignments/models.py`
- `src/emercard/modules/card_link_assignments/repository.py`
- `src/emercard/api/admin_card_routes.py`
- `src/emercard/api/user_card_routes.py`
- `src/emercard/api/profile_routes.py`
- `src/emercard/api/public_profile_routes.py`
- `src/emercard/api/emergency_routes.py`
- `src/emercard/db/normalize_legacy_links.py`
- `src/emercard/db/retire_legacy_access_fields.py`

## Domain model

```text
user
  -> one medical profile
  -> zero or more public links

profile
  -> many pending/active public links

card
  -> zero or one current assignment to one link
  -> custody history in card_custody_events

link
  -> zero or one card (pending links wait for admin binding)
  -> public access only after card binding, encoding verification, and delivery
```

A public link is the anonymous access token for one profile. A link starts pending, whether its initial purpose is `card` or `standalone`; “standalone” means it is not yet bound to a physical card, not that it may be publicly active without one. Each link can bind to at most one card and each card can have at most one current link. The link’s profile is the source of public authorization; `CardDocument.owner_id` remains delivery/custody metadata, not the anonymous lookup source.

## Card identity

- `serial` is the stable physical identifier.
- Raw public tokens are never persisted.
- The card document still accepts a legacy `token_hash` field for compatibility, but the code exposes it through the `CardDocument.legacy_token_hash` compatibility property.
- `token_revision` is not part of the current model.
- Encoding state is derived from `provisioned_at` and `encoding_verified_at`.

### Card lifecycle states

| State | Meaning |
|---|---|
| `unassigned` | Inventory card with no owner |
| `assigned` | Card is owned but not yet issued |
| `active` | Card is issued and active |
| `disabled` | Card is issued but disabled |
| `lost` | Card is terminal after loss report |
| `replaced` | Card is terminal after replacement |
| `void` | Card is retired before issue |

## Link lifecycle

`PublicAccessLink` records have a `purpose` and a status:

- `card` or `standalone`
- `pending`
- `active`
- `disabled`
- `revoked`
- `expired`

All newly created links begin pending. A link cannot activate while unbound. Activation requires card binding, physical encoding verification, and card delivery/issuance. Temporary disablement changes only `PublicAccessLink.status`; the assignment and physical card remain attached. Rebinding is allowed only before delivery; the prior link is revoked automatically. Delivered cards cannot detach or rebind. Standalone links are pending profile links awaiting admin card binding; they are not independently public.

## Admin workflow

1. Create a blank card.
2. Admin creates a pending profile link for a ready profile.
3. Admin binds the pending link to the blank card.
4. Write the one-time URL to the physical card.
5. Confirm the read-back URL.
6. Assign the verified card for delivery to the profile owner.
7. Issue/deliver the card.
8. Activate the attached link when public access should begin.

If a card is lost, disabled, detached, or replaced, the backend deactivates the assignment and disables or revokes the associated link.

Replacement creates a new card-purpose link for the replacement card and never silently reuses the old exposed token.

## User card controls

Authenticated users can see only their own issued, current cards. Safe card responses include status and link summaries, not raw tokens or hashes.

User actions are limited to the selected card:

- activate
- disable
- report lost

## Legacy and compatibility behavior

The backend still keeps compatibility paths for older deployments when link and assignment repositories are unavailable. In that fallback mode, card provisioning and verification use the legacy card token hash path.

Two operator commands handle the cutover:

- `emercard.db.normalize_legacy_links` copies known legacy hashes into public-link records and creates assignments.
- `emercard.db.retire_legacy_access_fields` drops obsolete legacy fields and indexes only after validation, backup, and rollback mapping are confirmed.

## Persistence and indexes

### `cards`

Card documents store physical identity, custody state, and compatibility metadata. The collection keeps indexes for:

- unique `serial`
- partial unique `token_hash` for legacy compatibility
- `owner_id`
- `status`
- `(owner_id, is_current)`
- `(owner_id, status)`
- replacement references
- encoding filtering

### `public_access_links`

The public-link collection stores:

- `profile_id`
- `purpose`
- `label`
- `token_hash`
- `status`
- actor/timestamp metadata

Indexes include profile/list lookups and a unique `token_hash` index.

### `card_link_assignments`

Assignments record the relationship between one card and one card-purpose link. Historical rows remain for auditability. Partial unique indexes enforce one active assignment per card and one active assignment per link.

### `card_custody_events`

Custody events are append-only and record ownership transitions such as assigned, reassigned, unassigned, issued, and voided.

## Security and output rules

- Raw tokens are returned only in one-time provisioning responses.
- Logs and safe responses must never expose raw tokens, hashes, cookies, or medical fields.
- Anonymous lookup response bodies contain only the allowlisted profile projection.
- Internal scan attribution is preserved through the link/assignment records, but it is not exposed in public responses.

## Verification

See [`verification.md`](verification.md) for the standard command set and MongoDB integration checks.