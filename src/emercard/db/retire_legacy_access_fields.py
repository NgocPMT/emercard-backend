"""Operator command to retire obsolete legacy card/profile token fields."""

from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

from emercard.core.config import get_settings
from emercard.core.types import utc_now
from emercard.db import Database
from emercard.db.indexes import (
    CARDS_TOKEN_HASH_INDEX,
    PROFILES_PUBLIC_TOKEN_INDEX,
)


class LegacyAccessRetirementError(RuntimeError):
    pass


async def run(
    *,
    apply: bool = False,
    validated: bool = False,
    backup_confirmed: bool = False,
    rollback_mapped: bool = False,
) -> dict[str, Any]:
    settings = get_settings()
    database = Database(settings)
    await database.start()
    try:
        cards_collection = database.database[settings.mongodb_cards_collection]
        profiles_collection = database.database[settings.mongodb_profiles_collection]

        cards_with_legacy_token = await cards_collection.count_documents(
            {"token_hash": {"$type": "string"}}
        )
        profiles_with_legacy_token = await profiles_collection.count_documents(
            {"public_access.token": {"$type": "string"}}
        )

        report: dict[str, Any] = {
            "apply": apply,
            "dry_run": not apply,
            "validated": validated,
            "backup_confirmed": backup_confirmed,
            "rollback_mapped": rollback_mapped,
            "cards_with_legacy_token": cards_with_legacy_token,
            "profiles_with_legacy_token": profiles_with_legacy_token,
            "indexes_to_drop": [CARDS_TOKEN_HASH_INDEX, PROFILES_PUBLIC_TOKEN_INDEX],
            "indexes_dropped": [],
            "cards_retired": 0,
            "profiles_retired": 0,
        }

        if not apply:
            return report

        if not (validated and backup_confirmed and rollback_mapped):
            raise LegacyAccessRetirementError(
                "legacy access fields can only be retired after validation, backup, "
                "and rollback mapping are confirmed"
            )

        for index_name, collection in (
            (CARDS_TOKEN_HASH_INDEX, cards_collection),
            (PROFILES_PUBLIC_TOKEN_INDEX, profiles_collection),
        ):
            try:
                await collection.drop_index(index_name)
            except Exception:
                pass
            else:
                report["indexes_dropped"].append(index_name)

        timestamp = utc_now()
        cards_update = await cards_collection.update_many(
            {"token_hash": {"$type": "string"}},
            {
                "$set": {"updated_at": timestamp},
                "$unset": {
                    # Link-backed cards retain physical provisioning and
                    # verification metadata; only the legacy card bearer hash
                    # is retired.
                    "token_hash": "",
                },
            },
        )
        profiles_update = await profiles_collection.update_many(
            {"public_access.token": {"$type": "string"}},
            {
                "$set": {
                    "public_access.enabled": False,
                    "updated_at": timestamp,
                },
                "$unset": {
                    "public_access.token": "",
                    "public_access.published_at": "",
                    "public_access.regenerated_at": "",
                },
            },
        )
        report["cards_retired"] = getattr(cards_update, "modified_count", 0)
        report["profiles_retired"] = getattr(profiles_update, "modified_count", 0)
        return report
    finally:
        await database.close()


async def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Retire obsolete legacy card/profile token fields and indexes"
    )
    parser.add_argument("--apply", action="store_true", help="Apply changes instead of dry-run")
    parser.add_argument(
        "--validated",
        action="store_true",
        help="Confirm migration validation has already completed",
    )
    parser.add_argument(
        "--backup-confirmed",
        action="store_true",
        help="Confirm a fresh backup has been taken",
    )
    parser.add_argument(
        "--rollback-mapped",
        action="store_true",
        help="Confirm rollback mappings are available",
    )
    args = parser.parse_args(argv)

    try:
        result = await run(
            apply=args.apply,
            validated=args.validated,
            backup_confirmed=args.backup_confirmed,
            rollback_mapped=args.rollback_mapped,
        )
    except LegacyAccessRetirementError as error:
        print(
            json.dumps(
                {"error": {"code": "migration.not_ready", "message": str(error)}},
                sort_keys=True,
            )
        )
        raise SystemExit(1) from None

    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
