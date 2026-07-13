# HTTP Contract

This document summarizes the implemented backend routes and response rules. It focuses on the current link-first behavior: card access is managed through public links and assignments, `/api/v1/public/{token}` is the canonical anonymous lookup, and `/api/v1/emergency/{token}` remains a compatibility adapter.

## Infrastructure

- `GET /health` returns `200` and `{ "status": "ok" }` without a database query.
- `GET /ready` returns `200` only after a successful MongoDB ping, otherwise `503` with `error.code=database_unavailable`.
- `GET /api/v1/meta` returns non-sensitive application and build metadata.

## Authentication

- `POST /api/v1/auth/register` returns `201` with `CurrentUserOutput` and provisions an empty profile.
- `POST /api/v1/auth/login` returns `200` with `CurrentUserOutput` and sets the `emercard_session` cookie.
- `GET /api/v1/me` returns the authenticated `CurrentUserOutput`.
- `POST /api/v1/auth/logout` returns `204` and expires the cookie.

## Medical profile

- `GET /api/v1/me/profile` returns the current user's sanitized medical profile.
- `PUT /api/v1/me/profile` fully replaces editable profile fields.
- `GET /api/v1/me/profile/public-preview` returns the public allowlist projection.
- `POST /api/v1/me/profile/public-preview-link` returns a one-time preview URL for the current profile.
- `POST /api/v1/me/profile/public-preview-link/generate` creates or reuses the standalone preview link.
- `POST /api/v1/me/profile/public-preview-link/regenerate` rotates the standalone preview link.
- `POST /api/v1/me/profile/public-preview-link/disable` disables the standalone preview link.

Profile routes derive ownership from the authenticated session. Profile responses never expose `public_access`, tokens, profile/user identifiers, or internal emergency-contact IDs.

## User cards

Every route below requires an authenticated user session. Ownership comes only from the session.

- `GET /api/v1/me/cards` returns the user's issued current controllable cards.
- `GET /api/v1/me/cards/{cardId}` returns one safe card projection.
- `POST /api/v1/me/cards/{cardId}/activate` activates an issued owned card from `assigned` or `disabled`.
- `POST /api/v1/me/cards/{cardId}/disable` disables an owned issued `active` card.
- `POST /api/v1/me/cards/{cardId}/lost` marks the selected card lost.

User card responses contain only safe identifiers, lifecycle timestamps, derived action flags, and link summaries. They never include raw tokens, token hashes, public URLs, owner/admin identifiers, custody history, replacement internals, or medical-profile data.

## Admin cards and link management

Every route below requires an authenticated administrator.

### Inventory and custody

- `POST /api/v1/admin/cards` creates a blank serial-only card and requires `Idempotency-Key`.
- `GET /api/v1/admin/cards` lists cards with safe filters.
- `GET /api/v1/admin/cards/{cardId}` returns safe card metadata and a safe owner summary.
- `POST /api/v1/admin/cards/{cardId}/assign` assigns a verified card to a user ID.
- `POST /api/v1/admin/cards/{cardId}/reassign` changes the owner before issue.
- `POST /api/v1/admin/cards/{cardId}/unassign` removes the pre-issue owner.
- `POST /api/v1/admin/cards/{cardId}/issue` issues the card.
- `POST /api/v1/admin/cards/{cardId}/void` retires the card before issue.
- `POST /api/v1/admin/cards/{cardId}/lost` marks the card lost.
- `POST /api/v1/admin/cards/{cardId}/replace` provisions a replacement card and returns the one-time URL.

### Card-link provisioning

- `POST /api/v1/admin/cards/{cardId}/provision-link` returns the raw public token and URL once with `Cache-Control: no-store`.
- `POST /api/v1/admin/cards/{cardId}/reprovision-link` rotates the card link before verification.
- `POST /api/v1/admin/cards/{cardId}/confirm-encoding` verifies a read-back `public_url`.
- `GET /api/v1/admin/cards/{cardId}/link` returns the safe card/link summary.
- `POST /api/v1/admin/cards/{cardId}/link/attach` attaches an existing card-purpose link.
- `POST /api/v1/admin/cards/{cardId}/link/detach` detaches the current link.
- `POST /api/v1/admin/cards/{cardId}/link/revoke` revokes the current link.

### Owner link management

- `GET /api/v1/admin/users/lookup?email=` returns a safe account summary.
- `GET /api/v1/admin/users/{user_id}/links` lists safe public links for that profile.
- `POST /api/v1/admin/users/{user_id}/links` creates a standalone or card-purpose link for that profile.

Safe admin responses never expose raw tokens, token hashes, or medical-profile data except through the explicit public-profile projection. Provisioning responses are the only admin responses that include a raw URL and they are marked `Cache-Control: no-store`.

## Public profile links

- `GET /api/v1/public/{token}` is the canonical anonymous lookup.
- Success returns `200` with `{ "profile": PublicProfileOutput }`.
- Invalid or unknown tokens return `404 public_profile.not_found`.
- Disabled links return `410 public_profile.disabled`.
- Pending, expired, revoked, or missing-profile cases return the corresponding neutral safe error.
- All public-profile responses use `Cache-Control: no-store`, `Pragma: no-cache`, `X-Robots-Tag: noindex, nofollow, noarchive`, `Referrer-Policy: no-referrer`, and `X-Content-Type-Options: nosniff`.
- Request logs use `/api/v1/public/{token}` as the route template.

## Anonymous emergency lookup

- `GET /api/v1/emergency/{token}` is a read-only compatibility adapter over public links.
- It hashes the raw bearer token before lookup and resolves only active card-purpose links.
- Success returns the same public profile projection used by `/api/v1/public/{token}`.
- Malformed, unknown, disabled, revoked, expired, pending, or missing-profile cases all return `404 emergency_profile.not_found`.
- Backend failures return `503 emergency_profile.service_unavailable`.
- All lookup responses use the same privacy headers as `/api/v1/public/{token}`.
- Anonymous requests are rate limited per direct peer.
- Request logs use `/api/v1/emergency/{token}` as the route template and never include the raw path, token, hash, query string, referrer, request body, or response body.
- New clients should use `/api/v1/public/{token}`.

## Authorization and transport

- Admin card routes use the shared admin authorization boundary.
- Authenticated normal users receive `403 auth.forbidden`; unauthenticated requests receive `401 auth.authentication_required`.
- Browser calls from the configured frontend origin must use credentialed requests.
- Errors contain a stable error code, Vietnamese human-readable message, optional sanitized validation details, and `request_id`.
- Frontend integrations should branch on `error.code`, not translated text.

See [`card-persistence.md`](card-persistence.md) for lifecycle gates, assignment rules, custody history, and the public-link/card relationship.
