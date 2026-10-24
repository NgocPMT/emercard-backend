"""Request and result models for anonymous scanner-location alerts."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator

from emercard.core.types import UtcDateTime
from emercard.modules.profiles.models import ProfileModel


class LocationAlertRequest(ProfileModel):
    """Only accept location data produced by the consenting browser."""

    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    accuracy_meters: float = Field(gt=0, le=10_000)
    occurred_at: datetime

    @field_validator("latitude", "longitude", "accuracy_meters")
    @classmethod
    def validate_finite(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("location values must be finite")
        return value

    @field_validator("occurred_at")
    @classmethod
    def validate_occurred_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("occurred_at must include timezone information")
        return value


class LocationAlertResponse(ProfileModel):
    """Neutral response that does not disclose recipients or provider state."""

    status: Literal["sent", "unavailable", "cooldown"]


class ReverseGeocodedLocation(ProfileModel):
    """Human-readable location and a safe map link for an alert."""

    nearby_place: str
    map_url: str


class LocationAlertResult(ProfileModel):
    """Internal delivery result used by the HTTP boundary."""

    status: Literal["sent", "unavailable", "cooldown"]
    provider_id: str | None = None
    recipient_count: int = 0
    nearby_place: str | None = None


class SmsDeliveryReceiptOutput(ProfileModel):
    recipient_name: str
    recipient_phone: str
    recipient_relationship: str | None = None
    status: Literal["sent", "failed"]
    provider_id: str | None = None


class LocationAlertIncidentOutput(ProfileModel):
    id: str
    created_at: UtcDateTime
    status: Literal["sent", "provider_error", "unavailable", "cooldown"]
    latitude: float | None = None
    longitude: float | None = None
    accuracy_meters: float | None = None
    nearby_place: str | None = None
    map_url: str | None = None
    recipients: list[SmsDeliveryReceiptOutput] = Field(
        default_factory=list[SmsDeliveryReceiptOutput]
    )


class LocationAlertHistoryOutput(ProfileModel):
    items: list[LocationAlertIncidentOutput]
    next_cursor: str | None = None
