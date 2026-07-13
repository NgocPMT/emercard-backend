# Phase 1 Database Models and Configuration

This document records the current persistence contract for the backend. It covers the link-first card model, the public-link collections, and the compatibility fields that remain until the migration retirement command is run.

## Collections

The Phase 1 collections are:

- `users`
- `medical_profiles`
- `public_access_links`
- `card_link_assignments`
- `cards`
- `card_custody_events`
- `idempotency_keys`

`medical_profiles.user_id` is unique, enforcing one profile per account. The legacy embedded `public_access` field remains on the profile document for compatibility, but anonymous lookup now uses `public_access_links`.

## User documents

A user document contains:

- `_id`
- `email`
- `password_hash`
- `role`
- `created_at`
- `updated_at`

Emails are canonicalized before persistence and are unique.

## Medical profile documents

A profile contains:

- `_id`
- `user_id`
- profile display and medical fields
- `public_access`
- `created_at`
- `updated_at`

`public_access` is a compatibility field for the legacy preview link. The new operator and HTTP flows use `public_access_links` instead of storing a new raw token on the profile document.

## Public access links

`public_access_links` stores independent public bearer links. A document contains:

- `_id`
- `profile_id`
- `purpose` (`card` or `standalone`)
- `label`
- `token_hash`
- `status`
- `created_by`
- `created_at`
- `updated_at`
- `activated_at`
- `disabled_at`
- `revoked_at`
- `expires_at`
- `expired_at`

The collection uses:

- a non-unique `profile_id` index
- a `(profile_id, purpose)` index
- a unique `token_hash` index
- a `status` index

## Card link assignments

`card_link_assignments` records the one-to-one relationship between a card and a card-purpose link. A document contains:

- `_id`
- `card_id`
- `public_access_link_id`
- `status` (`active`, `disabled`, `detached`)
- `attached_at`
- `updated_at`
- `attached_by_admin_id`
- `disabled_at`
- `disabled_by_admin_id`
- `detached_at`
- `detached_by_admin_id`
- `detach_reason`

Indexes enforce:

- one active assignment per `card_id`
- one active assignment per `public_access_link_id`
- historical rows are allowed for auditing

## Card documents

A card document stores physical identity, custody, and compatibility metadata:

- `_id`
- `serial`
- `owner_id`
- `token_hash` in storage, with `CardDocument.legacy_token_hash` / `token_hash` compatibility access in code
- `status`
- `is_current`
- `provisioned_at`
- `encoding_verified_at`
- `encoded_by_admin_id`
- `assigned_at`
- `activated_at`
- `disabled_at`
- `lost_at`
- `replaced_at`
- `issued_at`
- `issued_by_admin_id`
- `voided_at`
- `replaces_card_id`
- `replacement_card_id`
- `created_at`
- `updated_at`

`token_revision` is not part of the current model. The code still keeps the legacy token hash path for compatibility, but card access is now governed by public links and assignments.

Card indexes include:

- unique `serial`
- partial unique `token_hash` for string values
- `owner_id`
- `status`
- `(owner_id, is_current)`
- `(owner_id, status)`
- replacement references
- encoding filtering

## Custody events and idempotency

`card_custody_events` is append-only. It stores:

- `card_id`
- `event_type`
- `previous_owner_id`
- `new_owner_id`
- `performed_by_admin_id`
- `reason`
- `created_at`

`idempotency_keys.operation_key` is unique and stores the blank-card replay boundary.

## Validation limits

The initial demo limits remain centralized in `Settings`. See `configuration.md` for the current values.

## Public access and compatibility

- `public_access_links` is the canonical anonymous lookup store.
- `/api/v1/public/{token}` is the canonical HTTP entrypoint.
- `/api/v1/emergency/{token}` remains a compatibility adapter over public links.
- `medical_profiles.public_access` remains only for compatibility until the retirement command is run.

## Index initialization

Index creation is idempotent. The initialization command never drops or rebuilds data, and incompatible existing index options fail visibly.

See [`card-persistence.md`](card-persistence.md) for the lifecycle and business rules, and [`maintenance.md`](maintenance.md) for the operator commands that move or retire legacy data.