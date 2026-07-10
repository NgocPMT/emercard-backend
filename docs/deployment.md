# Demo deployment boundary

Plan 01 targets one Render web service connected to one MongoDB Atlas demo cluster. Provider resources must be created by the project team because account access, free-tier availability, cluster labels, and network policies are external to this repository.

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
- `EMERCARD_AUTH_SECRET`: generated or separately managed value of at least 32 characters; authentication is not implemented in Plan 01.
- `EMERCARD_ENVIRONMENT=demo`, `EMERCARD_DEBUG=false`, and `EMERCARD_MONGODB_TLS_REQUIRED=true`.

Render supplies `PORT`; the start command binds to it. The service health check is `/health` and the post-deploy database smoke check is `/ready`.

## Deployment verification

```bash
curl -fsS https://<render-service>.onrender.com/health
curl -i https://<render-service>.onrender.com/ready
```

Verify that `/health` is reachable over HTTPS, `/ready` reports database readiness, an unapproved browser origin does not receive an allowlisted CORS header, and logs contain no URI, secret, token, cookie, request body, or medical field values. Record the final service URL, Atlas database name, environment names, and provider constraints in the project handoff rather than in secrets.
