# HTTP Contract

## Infrastructure

- `GET /health` returns `200` and `{ "status": "ok" }` without a database query.
- `GET /ready` returns `200` only after a successful MongoDB ping, otherwise `503` with `error.code=database_unavailable`.
- `GET /api/v1/meta` returns non-sensitive application and build metadata.

## Authentication

- `POST /api/v1/auth/register` returns `201` with a direct `CurrentUserOutput` containing role `user`; registration also idempotently provisions one empty incomplete medical profile and does not authenticate the account.
- `POST /api/v1/auth/login` returns `200` with `CurrentUserOutput` and sets the short-lived `emercard_session` cookie.
- `GET /api/v1/me` returns the authenticated `CurrentUserOutput`.
- `POST /api/v1/auth/logout` returns `204` and expires the cookie.

## Medical profile

- `GET /api/v1/me/profile` returns the current user's sanitized medical profile.
- `PUT /api/v1/me/profile` fully replaces valid editable fields and permits incomplete drafts.
- `GET /api/v1/me/profile/public-preview` returns the stable public emergency projection.
- All profile routes derive ownership from the authenticated session.
- Profile `state` is `incomplete` or `ready_to_publish`; it is independent of card and legacy public-link state.
- Profile responses never expose `public_access`, tokens, profile/user identifiers, or internal emergency-contact IDs.
- Empty optional scalars are `null`, collections are `[]`, and public preview includes complete emergency-contact phone numbers.
- New emergency-contact phones must be exactly 10 digits and start with `0` (Vietnamese local format). Legacy stored phone values remain readable; replace them with a valid number through `PUT /api/v1/me/profile`.
- Missing provisioned profiles return `500 profile.provisioning_inconsistent`; persistence failures return `503 profile.service_unavailable`.

## Authorization and transport

- Admin card routes use the shared admin authorization boundary. Authenticated normal users receive `403 auth.forbidden`; unauthenticated requests receive `401 auth.authentication_required`.
- Browser calls from the exact configured frontend origin must use `fetch(..., { credentials: "include" })`; local development uses `http://localhost:4321`.
- Phase 1 logout is stateless: a copied JWT remains valid until its 15-minute expiry. Do not claim immediate revocation.
- Errors contain a stable error code, Vietnamese human-readable message, optional sanitized Vietnamese validation details, and `request_id`. Frontend integrations should branch on `error.code`, not translated text.
- CORS uses the configured exact origin allowlist with credentials enabled; wildcard origins and configured cookie domains are rejected by settings.

## User cards

Every route below requires an authenticated user session. Ownership is derived exclusively from the session; a request cannot supply an owner, status, token, hash, issuance, encoding, or admin field.

- `GET /api/v1/me/cards` returns `{ "cards": [] }` when the user has no issued current controllable cards.
- `GET /api/v1/me/cards/{cardId}` returns one safe card projection. Unknown, malformed, hidden, stale, or another user's card returns neutral `404 card.not_found`.
- `POST /api/v1/me/cards/{cardId}/activate` activates an issued, encoding-verified owned card from `assigned` or `disabled` when the profile state is `ready_to_publish`.
- `POST /api/v1/me/cards/{cardId}/disable` disables an owned issued `active` card.
- Repeated activation of an active card and repeated disablement of a disabled card return `200` without replacing the existing lifecycle timestamp.
- Activation and disablement are independent single-card operations; multiple cards may remain active and a sibling card is never changed.
- Existing active cards remain active when the profile later becomes incomplete. Activation and reactivation recheck profile readiness.

User card responses contain only `id`, `serial`, `status`, `is_current`, issuance/activation/disablement and audit timestamps, and derived action flags. They never contain token material, public URLs, owner/admin identifiers, custody history, replacement internals, or medical-profile data.

User-control failures use stable codes: `card.not_issued`, `card.encoding_not_verified`, `card.profile_not_ready`, `card.invalid_state_transition`, `card.terminal`, and `card.service_unavailable` as applicable. Cross-user access remains `card.not_found`.

## Admin cards

Every route below requires an authenticated administrator:

- `POST /api/v1/admin/cards` creates a blank serial-only card and requires `Idempotency-Key`.
- `POST /api/v1/admin/cards/{cardId}/provision-link` returns the raw public token and URL once with `Cache-Control: no-store`.
- `POST /api/v1/admin/cards/{cardId}/reprovision-link` returns a replacement URL once before verification.
- `POST /api/v1/admin/cards/{cardId}/confirm-encoding` verifies a read-back `public_url`.
- `GET /api/v1/admin/users/lookup?email=` returns a safe account summary without medical fields.
- `POST /api/v1/admin/cards/{cardId}/assign` assigns a verified card using a user ID.
- `POST /api/v1/admin/cards/{cardId}/reassign` accepts `new_owner_id` and one of `assignment_error`, `recipient_changed`, or `internal_correction` before issuance.
- `POST /api/v1/admin/cards/{cardId}/unassign`, `/issue`, and `/void` perform the corresponding custody operation.
- `GET /api/v1/admin/cards` supports status, owner, serial, current, derived encoding-state, issued, limit, and cursor filters.
- `GET /api/v1/admin/cards/{cardId}` returns safe card metadata and a safe owner summary.

Safe card responses never include raw tokens, token hashes, public URLs, medical-profile data, cookies, or authentication secrets. Custody history is persisted internally and is not currently included in card detail. Anonymous lookup and replacement HTTP routes remain deferred.

## Anonymous emergency lookup

- `GET /api/v1/emergency/{token}` is anonymous and does not require a session or bearer credential.
- The API hashes the raw bearer token before using the constrained card lookup. Only active, current, issued, encoding-verified cards with an owner resolve.
- Success returns `{ "profile": ... }` using the public medical allowlist. It includes `profile_updated_at` and excludes account/card identifiers, serials, token material, contact IDs, `public_access`, profile state, and private metadata.
- Malformed, unknown, unassigned, assigned, disabled, lost, replaced, void, non-current, ownerless, and missing-profile cases all return `404 emergency_profile.not_found` with the same neutral message. Dependency outages return `503 emergency_profile.service_unavailable`.
- All lookup responses use `Cache-Control: no-store`, `Pragma: no-cache`, `X-Robots-Tag: noindex, nofollow, noarchive`, `Referrer-Policy: no-referrer`, and `X-Content-Type-Options: nosniff`.
- Anonymous requests use an emergency-friendly per-direct-peer sliding-window rate limit and return `429 rate_limit.exceeded` without token-dependent detail. Forwarded headers are not trusted.
- Request logs use `/api/v1/emergency/{token}` as the route template and never include the raw path, token/hash, query string, referrer, request body, or response body.

See [`card-persistence.md`](card-persistence.md) for lifecycle gates, idempotency, event history, and manual NFC/QR encoding responsibilities.
