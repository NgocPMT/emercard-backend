# Configuration Contract

All environment-dependent values are loaded by the typed `Settings` object. Copy `.env.example` to `.env` for local work. Deployed environments must provide values through platform environment settings, not committed files.

## Deployment settings

For Atlas, set `EMERCARD_MONGODB_URI` to the TLS connection string and use a separate least-privilege demo database user. Deployed environments require:

- `EMERCARD_ENVIRONMENT=demo` (or `staging`/`production` as appropriate);
- `EMERCARD_DEBUG=false`;
- `EMERCARD_MONGODB_TLS_REQUIRED=true` in production;
- a random `EMERCARD_AUTH_SECRET` of at least 32 characters;
- `EMERCARD_AUTH_COOKIE_SECURE=true`;
- the exact deployed frontend origin in `EMERCARD_CORS_ORIGINS`;
- `EMERCARD_CORS_ALLOW_CREDENTIALS=true`;
- a host-only HTTP-only authentication cookie; cookie domains remain unset.

## Persistence defaults

The default MongoDB collections are `users`, `medical_profiles`, `cards`, `card_custody_events`, `idempotency_keys`, and `public_access_links`. The legacy `medical_profiles.public_access` field and index remain for compatibility. See [`database-models.md`](database-models.md) and [`card-persistence.md`](card-persistence.md) for persistence contracts.

`EMERCARD_PUBLIC_CARD_BASE_URL` configures the exact absolute URL prefix used for physical card links, for example `https://app.emercard.id.vn/e`. It must not contain a query or fragment. `EMERCARD_PUBLIC_PROFILE_BASE_URL` configures the quick-demo public-profile page URL prefix, for example `https://app.emercard.id.vn/e`; it must also end with `/e` and must not contain a query or fragment. Provisioning responses are the only API responses that contain a raw card URL/token and are marked `Cache-Control: no-store`.

`EMERCARD_MONGODB_PUBLIC_ACCESS_LINKS_COLLECTION` can rename the quick-demo profile-link collection when required, but it defaults to `public_access_links`.

Anonymous emergency lookup uses these bounded settings:

- `EMERCARD_EMERGENCY_TOKEN_MAX_LENGTH` defaults to `128`; generated Phase 1 tokens are 43 URL-safe characters.
- `EMERCARD_EMERGENCY_RATE_LIMIT_WINDOW_SECONDS` defaults to `60`.
- `EMERCARD_EMERGENCY_RATE_LIMIT_BURST` defaults to `30` requests per direct peer in that sliding window.

The in-process limiter is per worker and is not DDoS protection. Do not trust `X-Forwarded-For` unless a future deployment-specific trusted-proxy boundary is implemented; use edge/WAF controls for distributed abuse protection.

## Admin custody operations

Custody mutations write append-only events transactionally with the card mutation. Use a replica-set-capable MongoDB for admin operations that require event atomicity. Blank-card creation uses the durable `idempotency_keys` collection; operation keys currently have no TTL and must be retained according to the deployment's operational policy.

## Security handling

Do not log or commit MongoDB URIs, secrets, tokens, cookies, request bodies, or fictional medical fields. Do not use real medical data.
