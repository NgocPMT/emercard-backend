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

- Future admin routes must depend on the shared admin authorization boundary. Authenticated normal users receive `403 auth.forbidden`; unauthenticated requests receive `401 auth.authentication_required`. No admin feature routes are implemented yet.
- Browser calls from the exact configured frontend origin must use `fetch(..., { credentials: "include" })`; local development uses `http://localhost:4321`.
- Phase 1 logout is stateless: a copied JWT remains valid until its 15-minute expiry. Do not claim immediate revocation.
- Errors contain a stable error code, Vietnamese human-readable message, optional sanitized Vietnamese validation details, and `request_id`. Frontend integrations should branch on `error.code`, not translated text.
- CORS uses the configured exact origin allowlist with credentials enabled; wildcard origins and configured cookie domains are rejected by settings.

## Card boundary

Card persistence is an internal backend foundation only. See [`card-persistence.md`](card-persistence.md). No card HTTP routes or anonymous lookup are exposed yet.
