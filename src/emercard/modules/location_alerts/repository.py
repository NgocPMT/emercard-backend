"""Persistence for location-alert audit events and emergency incidents."""

from __future__ import annotations

import base64
import binascii
import json
from datetime import datetime, timedelta
from typing import Any, Protocol

from bson.objectid import ObjectId
from pymongo import DESCENDING

from emercard.core.config import Settings
from emercard.modules.location_alerts.models import (
    LocationAlertIncidentOutput,
    SmsDeliveryReceiptOutput,
)


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
        latitude: float | None = None,
        longitude: float | None = None,
        accuracy_meters: float | None = None,
        map_url: str | None = None,
        recipients: list[dict[str, Any]] | None = None,
    ) -> None: ...

    async def list_by_card_id(
        self,
        *,
        card_id: str,
        limit: int = 50,
        cursor: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]: ...


class MongoLocationAlertAuditRepository:
    """Store operational metadata and incident telemetry with automatic TTL expiration."""

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
        latitude: float | None = None,
        longitude: float | None = None,
        accuracy_meters: float | None = None,
        map_url: str | None = None,
        recipients: list[dict[str, Any]] | None = None,
    ) -> None:
        await self._collection.insert_one(
            {
                "_id": ObjectId(),
                "link_id": link_id,
                "card_id": card_id,
                "created_at": created_at,
                "expires_at": created_at + self._retention,
                "status": status,
                "nearby_place": nearby_place,
                "provider_id": provider_id,
                "location_bucket": location_bucket,
                "client_key_hash": client_key_hash,
                "latitude": latitude,
                "longitude": longitude,
                "accuracy_meters": accuracy_meters,
                "map_url": map_url,
                "recipients": recipients or [],
            }
        )

    async def list_by_card_id(
        self,
        *,
        card_id: str,
        limit: int = 50,
        cursor: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        if limit < 1 or limit > 100:
            raise ValueError("alert history limit must be between 1 and 100")
        query: dict[str, Any] = {
            "card_id": card_id,
        }
        if cursor is not None:
            created_at, event_id = _decode_cursor(cursor)
            query["$or"] = [
                {"created_at": {"$lt": created_at}},
                {"created_at": created_at, "_id": {"$lt": event_id}},
            ]
        records = await (
            self._collection.find(query)
            .sort([("created_at", DESCENDING), ("_id", DESCENDING)])
            .to_list(length=limit + 1)
        )
        next_cursor = None
        if len(records) > limit:
            page = records[:limit]
            last = page[-1]
            next_cursor = _encode_cursor(last["created_at"], last["_id"])
            records = page
        return records, next_cursor


def _encode_cursor(created_at: datetime, event_id: ObjectId) -> str:
    payload = json.dumps(
        {"created_at": created_at.isoformat(), "id": str(event_id)},
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_cursor(value: str) -> tuple[datetime, ObjectId]:
    if not value or len(value) > 512:
        raise ValueError("invalid alert history cursor")
    try:
        padding = "=" * (-len(value) % 4)
        payload = json.loads(base64.urlsafe_b64decode(value + padding).decode("utf-8"))
        created_at = datetime.fromisoformat(str(payload["created_at"]))
        event_id = ObjectId(str(payload["id"]))
        return created_at, event_id
    except (binascii.Error, KeyError, ValueError) as error:
        raise ValueError("invalid alert history cursor") from error


def to_location_alert_incident(record: dict[str, Any]) -> LocationAlertIncidentOutput:
    recipients = [
        SmsDeliveryReceiptOutput(
            recipient_name=r.get("recipient_name") or r.get("name", ""),
            recipient_phone=r.get("recipient_phone") or r.get("phone", ""),
            recipient_relationship=r.get("recipient_relationship") or r.get("relationship"),
            status=r.get("status", "sent"),
            provider_id=r.get("provider_id"),
        )
        for r in record.get("recipients", [])
    ]
    return LocationAlertIncidentOutput(
        id=str(record["_id"]),
        created_at=record["created_at"],
        status=record.get("status", "sent"),
        latitude=record.get("latitude"),
        longitude=record.get("longitude"),
        accuracy_meters=record.get("accuracy_meters"),
        nearby_place=record.get("nearby_place"),
        map_url=record.get("map_url"),
        recipients=recipients,
    )
