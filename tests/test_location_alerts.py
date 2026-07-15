from datetime import UTC, datetime

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from emercard.core.config import Settings
from emercard.main import create_app
from emercard.modules.location_alerts import (
    BrevoEmailDelivery,
    GoogleReverseGeocoder,
    LocationAlertLimiter,
    LocationAlertRequest,
    LocationAlertResult,
    LocationAlertService,
    ReverseGeocodedLocation,
)
from emercard.modules.profiles import ProfileDocument
from emercard.modules.public_links.lookup import PrivatePublicProfileLookupResult


class FakeResponse:
    def __init__(self, payload: object, status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("GET", "https://provider.test")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError("provider failure", request=request, response=response)

    def json(self) -> object:
        return self.payload


class FakeHttpClient:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.get_calls: list[dict[str, object]] = []
        self.post_calls: list[dict[str, object]] = []

    async def get(self, url: str, **kwargs: object) -> FakeResponse:
        self.get_calls.append({"url": url, **kwargs})
        return self.response

    async def post(self, url: str, **kwargs: object) -> FakeResponse:
        self.post_calls.append({"url": url, **kwargs})
        return self.response


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
async def test_location_alert_skips_contacts_without_email() -> None:
    service, geocoder, email, audit = service_for(profile(email=None))

    result = await service.send(token="public-token", request=request(), client_key="127.0.0.1")

    assert result.status == "unavailable"
    assert geocoder.calls == []
    assert email.calls == []
    assert audit.events[0]["status"] == "unavailable"


@pytest.mark.asyncio
async def test_google_reverse_geocoder_uses_v4_field_mask_and_prefers_formatted_address() -> None:
    settings = Settings(
        environment="test",
        google_geocoding_api_key=SecretStr("google-secret"),
    )
    client = FakeHttpClient(FakeResponse({"results": [{"formattedAddress": "Đường Đồng Khởi"}]}))

    result = await GoogleReverseGeocoder(settings, client=client).reverse(
        latitude=10.7769,
        longitude=106.7009,
    )

    assert result.nearby_place == "Đường Đồng Khởi"
    assert client.get_calls[0]["url"] == "https://geocode.googleapis.com/v4/geocode/location/10.7769000,106.7009000"
    assert client.get_calls[0]["params"] == {"languageCode": "vi"}
    assert client.get_calls[0]["headers"] == {
        "X-Goog-Api-Key": "google-secret",
        "X-Goog-FieldMask": "results.formattedAddress",
    }
    assert "google-secret" not in str(client.get_calls[0]["url"])


@pytest.mark.asyncio
async def test_google_reverse_geocoder_falls_back_without_a_result() -> None:
    settings = Settings(environment="test", google_geocoding_api_key=SecretStr("google-secret"))
    client = FakeHttpClient(FakeResponse({"results": []}))

    result = await GoogleReverseGeocoder(settings, client=client).reverse(
        latitude=10,
        longitude=20,
    )

    assert result.nearby_place == "vị trí được chia sẻ"
    assert result.map_url == "https://www.google.com/maps?q=10.0000000%2C20.0000000"


@pytest.mark.asyncio
async def test_brevo_email_contains_only_alert_context() -> None:
    settings = Settings(
        environment="test",
        brevo_api_key=SecretStr("brevo-secret"),
        brevo_sender_email="alerts@example.com",
        brevo_reply_to_email="reply@example.com",
    )
    client = FakeHttpClient(FakeResponse({"messageId": "message-1"}))

    result = await BrevoEmailDelivery(settings, client=client).send_location_alert(
        recipient="contact@example.com",
        display_name="Alex Example",
        nearby_place="Đường Đồng Khởi",
        map_url="https://www.google.com/maps?q=10,20",
        occurred_at=datetime(2026, 1, 1, 10, 30, tzinfo=UTC),
        accuracy_meters=25,
    )

    assert result == "message-1"
    payload = client.post_calls[0]["json"]
    assert isinstance(payload, dict)
    assert payload["to"] == [{"email": "contact@example.com"}]
    assert "Đường Đồng Khởi" in payload["textContent"]
    assert "https://www.google.com/maps?q=10,20" in payload["textContent"]
    assert "brevo-secret" not in str(payload)


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
