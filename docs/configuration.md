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

The default MongoDB collections are `users`, `medical_profiles`, and `cards`. The legacy `medical_profiles.public_access` field and index remain for compatibility. See [`database-models.md`](database-models.md) and [`card-persistence.md`](card-persistence.md) for persistence contracts.

## Security handling

Do not log or commit MongoDB URIs, secrets, tokens, cookies, request bodies, or fictional medical fields. Do not use real medical data.
