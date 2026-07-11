# Deployment Boundary

The current backend targets one Render web service connected to one MongoDB Atlas demo cluster. Provider resources must be created by the project team because account access, free-tier availability, cluster labels, and network policies are external to this repository.

The intended Render build command is `uv sync --locked --no-dev`; the start command is `uv run --no-dev uvicorn emercard.main:app --host 0.0.0.0 --port $PORT`; and the health check path is `/health`. Keep Atlas network access and the final frontend URL restricted to the project's deployment requirements. Record provider-specific service names and URLs in the project handoff rather than committing credentials.

## Current backend scope

The backend includes the `users`, `medical_profiles`, and `cards` persistence contracts, explicit `user`/`admin` roles, registration-time empty-profile provisioning, typed repositories, cookie-based authentication endpoints, secure card serial/token primitives, independent card lifecycle transitions, and isolated verification.

The following remain deferred: admin/user card endpoints, anonymous public card lookup, legacy token hashing/migration/removal, QR/NFC operations, inventory, scans, audits, Redis, messaging, encryption, deployment automation changes, and frontend integration. Legacy profile-owned public-link persistence and indexes remain unchanged for compatibility until the card-backed lookup replacement is approved.

## Atlas checklist

1. Create a clearly named EmerCard demo project and cluster.
2. Use a demo database name distinct from local and future test databases.
3. Create a database user scoped to the demo database where Atlas permits it.
4. Restrict network access to the selected Render deployment path as far as the demo platform allows.
5. Store the TLS URI only in Render's `EMERCARD_MONGODB_URI` secret.
6. Use fictional demo data only and document a reset by dropping/recreating the demo database through an approved operator workflow.

Never commit the Atlas URI, database credentials, real medical information, or reset credentials.

## Render checklist

The included `render.yaml` defines the intended service. Configure these values in Render:

- `EMERCARD_MONGODB_URI`: Atlas TLS URI.
- `EMERCARD_MONGODB_DATABASE`: final demo database name.
- `EMERCARD_CORS_ORIGINS`: JSON list containing only the deployed frontend origin(s).
- `EMERCARD_AUTH_SECRET`: generated or separately managed value of at least 32 characters.
- `EMERCARD_ENVIRONMENT=demo`, `EMERCARD_DEBUG=false`, and `EMERCARD_MONGODB_TLS_REQUIRED=true`.

Render supplies `PORT`; the start command binds to it. The service health check is `/health` and the post-deploy database smoke check is `/ready`.

## Deployment verification

```bash
curl -fsS https://<render-service>.onrender.com/health
curl -i https://<render-service>.onrender.com/ready
```

Verify that `/health` is reachable over HTTPS, `/ready` reports database readiness, an unapproved browser origin does not receive an allowlisted CORS header, and logs contain no URI, secret, token, cookie, request body, or medical field values. Record the final service URL, Atlas database name, environment names, and provider constraints in the project handoff rather than in secrets.
