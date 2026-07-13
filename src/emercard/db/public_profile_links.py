"""Operator command for profile-backed public links."""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict
from typing import Any

from bson.errors import InvalidId
from bson.objectid import ObjectId

from emercard.core.config import get_settings
from emercard.db import Database
from emercard.modules.profiles import ProfileRepository
from emercard.modules.public_links import (
    PublicAccessLinkRepository,
    PublicProfileError,
    PublicProfileLinkService,
)


async def run(action: str, *, profile_id: str) -> dict[str, Any]:
    settings = get_settings()
    database = Database(settings)
    await database.start()
    try:
        service = PublicProfileLinkService(
            PublicAccessLinkRepository(database.database, settings),
            ProfileRepository(database.database, settings),
            public_profile_base_url=settings.public_profile_base_url,
        )
        if action == "generate":
            result = await service.generate(profile_id=profile_id)
        elif action == "regenerate":
            result = await service.regenerate(profile_id=profile_id)
        elif action == "disable":
            result = await service.disable(profile_id=profile_id)
        else:  # pragma: no cover - argparse restricts choices
            raise ValueError(f"unsupported action: {action}")
        payload = asdict(result)
        payload.pop("raw_token", None)
        return payload
    finally:
        await database.close()


async def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Manage profile-backed public links")
    parser.add_argument("action", choices=["generate", "regenerate", "disable"])
    parser.add_argument("--profile-id", required=True)
    args = parser.parse_args(argv)

    try:
        ObjectId(args.profile_id)
    except InvalidId, TypeError:
        print(
            json.dumps(
                {
                    "error": {
                        "code": "public_profile.not_found",
                        "message": "Hồ sơ công khai không khả dụng.",
                    }
                },
                sort_keys=True,
            )
        )
        raise SystemExit(1) from None

    try:
        result = await run(args.action, profile_id=args.profile_id)
    except PublicProfileError as error:
        print(
            json.dumps(
                {"error": {"code": error.code, "message": error.message}},
                sort_keys=True,
            )
        )
        raise SystemExit(error.status_code) from None

    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
