# Phase 1 Database Models and Configuration

This document records the implementation defaults for Plan 02. It is a persistence contract for the later authentication and profile endpoint work; it does not add HTTP endpoints or make the demo suitable for real medical data.

## Collections

Only these Phase 1 feature collections are used:

- `users`
- `medical_profiles`

`medical_profiles.user_id` is unique, enforcing one profile per account. Public-link state is embedded in the profile; there is no separate link collection.

## User contract

A user document contains `_id`, canonical `email`, `password_hash`, `created_at`, and `updated_at`.

Canonical email is trimmed and fully lowercased. Provider-specific transformations such as removing dots or plus tags are not applied. The database stores only the canonical value and enforces uniqueness with a unique index. Password inputs are separate from persistence models; only a password hash may be persisted, and plaintext or recoverable password material is never returned.

## Medical profile contract

A profile contains `_id`, `user_id`, `display_name`, `birth_year`, `gender`, `blood_type`, `critical_allergies`, `important_conditions`, `critical_medications`, `emergency_note`, `emergency_contacts`, `public_access`, `created_at`, and `updated_at`.

The implementation defaults are:

- `birth_year` is stored rather than a stale calculated age.
- Gender values are `female`, `male`, `non_binary`, and `prefer_not_to_say`.
- Blood types are `A+`, `A-`, `B+`, `B-`, `AB+`, `AB-`, `O+`, and `O-`.
- Medical lists may be empty but are always present in persistence documents.
- A draft can be saved without an emergency contact; publication requires at least one complete contact.
- Publication is explicit rather than automatic on save.
- A disabled link retains its token and can be re-enabled after the profile is valid.
- Regeneration immediately invalidates the old token.

These values are the Plan 02 implementation defaults. Endpoint work should preserve them unless product/design explicitly changes the contract.

## Validation limits

The initial demo limits are centralized in `Settings`:

| Value | Limit |
|---|---:|
| Display name | 120 characters |
| Emergency note | 500 characters |
| Medical list item | 120 characters |
| Items per medical list | 10 |
| Emergency contacts | 5 |
| Contact name | 100 characters |
| Contact relationship | 80 characters |
| Contact phone | 32 characters |
| Birth year | 1900 through the current year |

Phone values are accepted in conservative international-looking form: digits, spaces, `+`, `-`, parentheses, and dots. No provider-specific normalization is performed.

## Public access

`public_access` contains `token`, `enabled`, `published_at`, and `regenerated_at`. Tokens are generated from a cryptographically secure random source and are not logged. A profile without a token is unpublished. Only enabled tokens resolve through the repository lookup. Phase 1 stores the raw token temporarily so the authenticated dashboard can display/copy the link; Phase 2 must migrate to hashed-token lookup.

The public response is an explicit allowlist of emergency-page fields. It excludes `_id`, `user_id`, password/account data, public token, publication metadata, and persistence timestamps.

## Derived profile state

Completeness is not persisted. The shared evaluator returns:

- `incomplete`
- `ready_to_publish`
- `published`
- `published_disabled`

A complete profile requires display name, accepted birth year, approved gender, approved blood type, and at least one complete emergency contact. Medical lists may remain empty under this Phase 1 default.

## Indexes and initialization

Required indexes are:

- unique `users.email`;
- unique `medical_profiles.user_id`;
- unique sparse/partial `medical_profiles.public_access.token`, allowing profiles without a token.

Initialization is idempotent and never drops or rebuilds data. The application may run it on startup only when configured; an explicit command is the deployment-safe default.

## Deferred decisions

Authentication transport is prepared for a secure HTTP-only cookie, with final cookie/CORS values dependent on the deployed frontend origin. MongoDB JSON Schema validation is intentionally omitted; Pydantic validation plus indexes are the Phase 1 baseline.
