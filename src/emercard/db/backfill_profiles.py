"""Idempotently provision missing medical profiles for existing users."""

import asyncio
import json

from emercard.core.config import get_settings
from emercard.db import Database
from emercard.modules.profiles.repository import ProfileRepository
from emercard.modules.users.repository import UserRepository


async def backfill() -> dict[str, int]:
    """Ensure every existing user has one empty profile and return safe counts."""

    settings = get_settings()
    database = Database(settings)
    await database.start()
    try:
        users = UserRepository(database.database, settings)
        profiles = ProfileRepository(database.database, settings)
        user_ids = await users.find_all_ids()
        created = 0
        for user_id in user_ids:
            if await profiles.find_by_user_id(user_id) is None:
                await profiles.ensure_for_user(user_id=user_id)
                created += 1
        return {"users_scanned": len(user_ids), "profiles_created": created}
    finally:
        await database.close()


async def main() -> None:
    print(json.dumps(await backfill(), sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
