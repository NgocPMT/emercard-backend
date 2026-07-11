# Maintenance Commands

Run these commands from `emercard-backend/` with the virtual environment activated.

## Backfill profiles

Backfill profiles for existing users with the idempotent maintenance command:

```bash
uv run python -m emercard.db.backfill_profiles
```

## Seed the initial admin

Export `EMERCARD_ADMIN_EMAIL` and `EMERCARD_ADMIN_PASSWORD` only for this command. The script never changes an existing account password or elevates a normal user:

```bash
EMERCARD_ADMIN_EMAIL=admin@example.com \
EMERCARD_ADMIN_PASSWORD='use-a-strong-password' \
uv run python -m emercard.db.seed_admin
```

The command prints only a status and email. It returns `already_exists` when the admin account is already present and fails safely if the email belongs to a normal user.
