from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from emercard.core.config import Settings
from emercard.main import create_app
from emercard.modules.location_alerts import (
    LocationAlertLimiter,
    LocationAlertRequest,
    LocationAlertResult,
    LocationAlertService,
    ReverseGeocodedLocation,
)
from emercard.modules.profiles import ProfileDocument
from emercard.modules.public_links.lookup import PrivatePublicProfileLookupResult


class FakeLookup:
    def __init__(self, profile: ProfileDocument) -> None:
        self.profile = profile

    async def lookup_private(self, token: str) -> PrivatePublicProfileLookupResult:
        return PrivatePublicProfileLookupResult(
            profile=self.profile,
            link_id="507f1f77bcf86cd799439011",
            card_id="507f1f77bcf86cd799439012",
        )


class FakeGeocoder:
    def __init__(self) -> None:
        self.calls: list[tuple[float, float]] = []

    async def reverse(self, *, latitude: float, longitude: float) -> ReverseGeocodedLocation:
        self.calls.append((latitude, longitude))
        return ReverseGeocodedLocation(
            nearby_place="Ben Thanh Market, Ho Chi Minh City",
            map_url="https://www.google.com/maps?q=10.77,106.69",
        )


class FakeEmailDelivery:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def send_location_alert(self, **kwargs: object) -> str:
        self.calls.append(kwargs)
        return "<brevo-message-id>"


class FakeAudit:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    async def record(self, **kwargs: object) -> None:
        self.events.append(kwargs)


def profile(*, enabled: bool = True, email: str | None = "contact@example.com") -> ProfileDocument:
    return ProfileDocument.model_validate(
        {
            "_id": "507f1f77bcf86cd799439011",
            "user_id": "507f1f77bcf86cd799439012",
            "display_name": "Alex Example",
            "birth_year": 1995,
            "gender": "prefer_not_to_say",
            "blood_type": "O+",
            "critical_allergies": [],
            "important_conditions": [],
            "critical_medications": [],
            "location_alerts_enabled": enabled,
            "emergency_contacts": [
                {
                    "name": "Sam Example",
                    "relationship": "Friend",
                    "phone": "0901234567",
                    "email": email,
                }
            ],
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        }
    )


def request() -> LocationAlertRequest:
    return LocationAlertRequest(
        latitude=10.772,
        longitude=106.698,
        accuracy_meters=25,
        occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def service_for(
    profile_document: ProfileDocument,
) -> tuple[LocationAlertService, FakeGeocoder, FakeEmailDelivery, FakeAudit]:
    geocoder = FakeGeocoder()
    email = FakeEmailDelivery()
    audit = FakeAudit()
    service = LocationAlertService(
        lookup=FakeLookup(profile_document),
        geocoder=geocoder,
        email_delivery=email,
        audit_repository=audit,
        limiter=LocationAlertLimiter(
            token_cooldown_seconds=300,
            ip_window_seconds=3600,
            ip_burst=5,
        ),
    )
    return service, geocoder, email, audit


@pytest.mark.parametrize(
    "field,value",
    [("latitude", 91), ("longitude", -181), ("accuracy_meters", 0)],
)
def test_location_alert_request_rejects_invalid_coordinates(field: str, value: float) -> None:
    values = request().model_dump()
    values[field] = value
    with pytest.raises(ValidationError):
        LocationAlertRequest.model_validate(values)


@pytest.mark.asyncio
async def test_location_alert_sends_nearby_place_and_map_link_without_public_contact_data() -> None:
    service, geocoder, email, audit = service_for(profile())

    result = await service.send(token="public-token", request=request(), client_key="127.0.0.1")

    assert result == LocationAlertResult(
        status="sent",
        provider_id="<brevo-message-id>",
        recipient_count=1,
        nearby_place="Ben Thanh Market, Ho Chi Minh City",
    )
    assert geocoder.calls == [(10.772, 106.698)]
    assert email.calls[0]["recipient"] == "contact@example.com"
    assert email.calls[0]["nearby_place"] == "Ben Thanh Market, Ho Chi Minh City"
    assert audit.events[0]["status"] == "sent"
    assert "public-token" not in str(audit.events[0])
    assert "contact@example.com" not in str(audit.events[0])


@pytest.mark.asyncio
async def test_location_alert_requires_owner_opt_in_and_does_not_call_providers() -> None:
    service, geocoder, email, audit = service_for(profile(enabled=False))

    result = await service.send(token="public-token", request=request(), client_key="127.0.0.1")

    assert result.status == "unavailable"
    assert geocoder.calls == []
    assert email.calls == []
    assert audit.events[0]["status"] == "unavailable"


@pytest.mark.asyncio
async def test_location_alert_cooldown_prevents_duplicate_delivery() -> None:
    service, geocoder, email, _ = service_for(profile())

    first = await service.send(token="public-token", request=request(), client_key="127.0.0.1")
    second = await service.send(token="public-token", request=request(), client_key="127.0.0.1")

    assert first.status == "sent"
    assert second.status == "cooldown"
    assert len(geocoder.calls) == 1
    assert len(email.calls) == 1


def test_location_alert_route_never_returns_contact_email() -> None:
    service, _, _, _ = service_for(profile())
    settings = Settings(
        environment="test",
        auth_secret=SecretStr("test-auth-secret-012345678901234567890"),
        cors_origins=["http://localhost:4321"],
    )
    app = create_app(settings=settings, location_alert_service=service)
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/public/public-token/location-alert",
            json={
                "latitude": 10.772,
                "longitude": 106.698,
                "accuracy_meters": 25,
                "occurred_at": "2026-01-01T00:00:00Z",
            },
        )

    assert response.status_code == 200
    assert response.json() == {"status": "sent"}
    assert "contact@example.com" not in response.text
