from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from emercard.core.config import Settings
from emercard.db import Database


@pytest.mark.asyncio
async def test_database_creates_one_async_client_and_closes_it() -> None:
    settings = Settings(environment="test")
    client = MagicMock()
    client.close = AsyncMock()
    database_handle = object()
    client.get_database.return_value = database_handle

    with patch("emercard.db.lifecycle.AsyncMongoClient", return_value=client) as client_factory:
        database = Database(settings)
        await database.start()

    client_factory.assert_called_once()
    assert database.database is database_handle
    await database.close()
    client.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_database_ping_returns_false_when_command_fails() -> None:
    settings = Settings(environment="test")
    database = Database(settings)
    client = MagicMock()
    database_handle = AsyncMock()
    database_handle.command.side_effect = TimeoutError("database unavailable")
    client.get_database.return_value = database_handle

    with patch("emercard.db.lifecycle.AsyncMongoClient", return_value=client):
        await database.start()

    assert await database.ping() is False
