# Maintenance Commands

Run these commands from `emercard-backend/` with the virtual environment activated.

## Backfill profiles

Backfill missing profiles for existing users:

```bash
uv run python -m emercard.db.backfill_profiles
```

## Manage profile-backed public links

Generate, regenerate, or disable the standalone preview link for a profile:

```bash
uv run python -m emercard.db.public_profile_links generate --profile-id <profile-id>
uv run python -m emercard.db.public_profile_links regenerate --profile-id <profile-id>
uv run python -m emercard.db.public_profile_links disable --profile-id <profile-id>
```

The command prints safe JSON. It does not print the raw token in error paths.

## Normalize legacy card and profile tokens

Normalize older card and profile token hashes into the link-first collections:

```bash
uv run python -m emercard.db.normalize_legacy_links
uv run python -m emercard.db.normalize_legacy_links --apply
```

- The default mode is dry-run.
- `--apply` writes public-link and assignment records.
- Shared legacy hashes cause the command to fail safely instead of silently completing.
- The command avoids logging raw tokens and hashes.

## Retire obsolete legacy access fields

Retire the old profile-token and card-token fields only after validation, backup, and rollback mapping are confirmed:

```bash
uv run python -m emercard.db.retire_legacy_access_fields
uv run python -m emercard.db.retire_legacy_access_fields \
  --apply \
  --validated \
  --backup-confirmed \
  --rollback-mapped
```

The command drops the obsolete token indexes and clears the legacy fields only in apply mode. Dry-run mode reports what would be removed.

## Seed the initial admin

Export `EMERCARD_ADMIN_EMAIL` and `EMERCARD_ADMIN_PASSWORD` only for this command. The script never changes an existing account password or elevates a normal user:

```bash
EMERCARD_ADMIN_EMAIL=admin@example.com \
EMERCARD_ADMIN_PASSWORD='use-a-strong-password' \
uv run python -m emercard.db.seed_admin
```

The command prints only a status and email. It returns `already_exists` when the admin account is already present and fails safely if the email belongs to a normal user.