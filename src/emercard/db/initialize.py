"""Explicit command for Phase 1 collection/index initialization."""

import asyncio

from emercard.core.config import get_settings
from emercard.db import Database, initialize_indexes


async def initialize() -> None:
    """Initialize indexes using the application-managed database lifecycle."""

    settings = get_settings()
    database = Database(settings)
    await database.start()
    try:
        await initialize_indexes(database.database, settings)
    finally:
        await database.close()


if __name__ == "__main__":
    asyncio.run(initialize())
