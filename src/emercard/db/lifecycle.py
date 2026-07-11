"""Centralized MongoDB async client lifecycle."""

from collections.abc import Mapping
from typing import Any

from pymongo import AsyncMongoClient

from emercard.core.config import Settings


class Database:
    """Own the sole MongoDB client and database handle for the application."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: AsyncMongoClient[Mapping[str, Any]] | None = None
        self._database: Any = None

    async def start(self) -> None:
        """Create the client without forcing startup to wait for a network ping."""

        options: dict[str, Any] = {
            "serverSelectionTimeoutMS": self._settings.mongodb_server_selection_timeout_ms,
            "minPoolSize": self._settings.mongodb_min_pool_size,
            "maxPoolSize": self._settings.mongodb_max_pool_size,
            "tz_aware": True,
        }
        if self._settings.mongodb_tls_required:
            options["tls"] = True
        self._client = AsyncMongoClient(self._settings.mongodb_uri, **options)
        self._database = self._client.get_database(self._settings.mongodb_database)

    async def ping(self) -> bool:
        """Return whether MongoDB responds, without exposing driver errors to callers."""

        if self._database is None:
            return False
        try:
            await self._database.command("ping")
        except Exception:
            return False
        return True

    @property
    def database(self) -> Any:
        """Expose the selected database for future repositories only."""

        if self._database is None:
            raise RuntimeError("database lifecycle has not started")
        return self._database

    async def close(self) -> None:
        """Close the async client and release the database handle."""

        if self._client is not None:
            await self._client.close()
        self._client = None
        self._database = None
