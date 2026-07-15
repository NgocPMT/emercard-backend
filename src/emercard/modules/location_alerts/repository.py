"""Persistence for minimized location-alert audit events."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Protocol

from emercard.core.config import Settings


class LocationAlertAuditRepository(Protocol):
    async def record(
        self,
        *,
        link_id: str,
        card_id: str | None,
        created_at: datetime,
        status: str,
        nearby_place: str | None,
        provider_id: str | None,
        location_bucket: str | None,
        client_key_hash: str,
    ) -> None: ...


class MongoLocationAlertAuditRepository:
    """Store only operational metadata; TTL removes records automatically."""

    def __init__(self, database: Any, settings: Settings) -> None:
        self._collection = database[settings.mongodb_location_alert_audits_collection]
        self._retention = timedelta(seconds=settings.location_alert_audit_retention_seconds)

    async def record(
        self,
        *,
        link_id: str,
        card_id: str | None,
        created_at: datetime,
        status: str,
        nearby_place: str | None,
        provider_id: str | None,
        location_bucket: str | None,
        client_key_hash: str,
    ) -> None:
        await self._collection.insert_one(
            {
                "link_id": link_id,
                "card_id": card_id,
                "created_at": created_at,
                "expires_at": created_at + self._retention,
                "status": status,
                "nearby_place": nearby_place,
                "provider_id": provider_id,
                "location_bucket": location_bucket,
                "client_key_hash": client_key_hash,
            }
        )
