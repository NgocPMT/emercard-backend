from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from emercard.core.config import Settings
from emercard.main import create_app
from emercard.modules.location_alerts import (
    AndroidSmsDelivery,
    LocationAlertExternalError,
    LocationAlertLimiter,
    LocationAlertRequest,
    LocationAlertResult,
    LocationAlertService,
    LocationIQReverseGeocoder,
    MongoLocationAlertAuditRepository,
    ReverseGeocodedLocation,
    SmsProviderError,
    SpeedSmsDelivery,
    format_location_alert_sms,
    strip_vietnamese_accents,
)
from emercard.modules.location_alerts.providers import LocationProviderError
from emercard.modules.profiles import EmergencyContactDocument, ProfileDocument
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


class FakeSmsDelivery:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def send_location_alert(self, **kwargs: object) -> str:
        self.calls.append(kwargs)
        return "<speedsms-tran-id>"


class FakeAudit:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    async def record(self, **kwargs: object) -> None:
        self.events.append(kwargs)


def profile(*, enabled: bool = True, phone: str | None = "0901234567") -> ProfileDocument:
    contacts: list[dict[str, object]] = []
    if phone:
        contacts.append(
            {
                "name": "Sam Example",
                "relationship": "Friend",
                "phone": phone,
                "email": "contact@example.com",
            }
        )
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
            "emergency_contacts": contacts,
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
) -> tuple[LocationAlertService, FakeGeocoder, FakeSmsDelivery, FakeAudit]:
    geocoder = FakeGeocoder()
    sms = FakeSmsDelivery()
    audit = FakeAudit()
    service = LocationAlertService(
        lookup=FakeLookup(profile_document),
        geocoder=geocoder,
        sms_delivery=sms,
        audit_repository=audit,
        limiter=LocationAlertLimiter(
            token_cooldown_seconds=300,
            ip_window_seconds=3600,
            ip_burst=5,
        ),
    )
    return service, geocoder, sms, audit


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
    service, geocoder, sms, audit = service_for(profile())

    result = await service.send(token="public-token", request=request(), client_key="127.0.0.1")

    assert result == LocationAlertResult(
        status="sent",
        provider_id="<speedsms-tran-id>",
        recipient_count=1,
        nearby_place="Ben Thanh Market, Ho Chi Minh City",
    )
    assert geocoder.calls == [(10.772, 106.698)]
    assert sms.calls[0]["recipient_phone"] == "0901234567"
    assert sms.calls[0]["nearby_place"] == "Ben Thanh Market, Ho Chi Minh City"
    assert audit.events[0]["status"] == "sent"
    assert "public-token" not in str(audit.events[0])
    assert "0901234567" not in str(audit.events[0])


@pytest.mark.asyncio
async def test_location_alert_requires_owner_opt_in_and_does_not_call_providers() -> None:
    service, geocoder, sms, audit = service_for(profile(enabled=False))

    result = await service.send(token="public-token", request=request(), client_key="127.0.0.1")

    assert result.status == "unavailable"
    assert geocoder.calls == []
    assert sms.calls == []
    assert audit.events[0]["status"] == "unavailable"


@pytest.mark.asyncio
async def test_location_alert_skips_contacts_without_phone() -> None:
    service, geocoder, sms, audit = service_for(profile(phone=None))

    result = await service.send(token="public-token", request=request(), client_key="127.0.0.1")

    assert result.status == "unavailable"
    assert geocoder.calls == []
    assert sms.calls == []
    assert audit.events[0]["status"] == "unavailable"


@pytest.mark.asyncio
async def test_locationiq_reverse_geocoder_requests_vietnamese_and_parses_display_name() -> None:
    settings = Settings(
        _env_file=None,
        environment="test",
        locationiq_api_key=SecretStr("locationiq-secret"),
    )
    client = FakeHttpClient(
        FakeResponse({"display_name": "Nhà thờ Đức Bà Sài Gòn, Quận 1, TP Hồ Chí Minh"})
    )

    result = await LocationIQReverseGeocoder(settings, client=client).reverse(
        latitude=10.7769,
        longitude=106.7009,
    )

    assert result.nearby_place == "Nhà thờ Đức Bà Sài Gòn, Quận 1, TP Hồ Chí Minh"
    assert result.map_url == "https://www.google.com/maps?q=10.7769000%2C106.7009000"
    assert client.get_calls[0]["url"] == "https://us1.locationiq.com/v1/reverse"
    assert client.get_calls[0]["params"] == {
        "key": "locationiq-secret",
        "lat": "10.7769000",
        "lon": "106.7009000",
        "format": "json",
        "accept-language": "vi",
    }


@pytest.mark.asyncio
async def test_locationiq_reverse_geocoder_falls_back_on_empty_display_name_or_404() -> None:
    settings = Settings(
        _env_file=None,
        environment="test",
        locationiq_api_key=SecretStr("locationiq-secret"),
    )

    # Case 1: 404 Unable to geocode (e.g. coordinates in open sea)
    client_404 = FakeHttpClient(FakeResponse({"error": "Unable to geocode"}, status_code=404))
    result_404 = await LocationIQReverseGeocoder(settings, client=client_404).reverse(
        latitude=10.0,
        longitude=20.0,
    )
    assert result_404.nearby_place == "vị trí được chia sẻ"
    assert result_404.map_url == "https://www.google.com/maps?q=10.0000000%2C20.0000000"

    # Case 2: empty or whitespace display_name
    client_empty = FakeHttpClient(FakeResponse({"display_name": "   "}))
    result_empty = await LocationIQReverseGeocoder(settings, client=client_empty).reverse(
        latitude=10.0,
        longitude=20.0,
    )
    assert result_empty.nearby_place == "vị trí được chia sẻ"


@pytest.mark.asyncio
async def test_locationiq_reverse_geocoder_falls_back_when_api_key_missing() -> None:
    settings = Settings(
        _env_file=None,
        environment="test",
        locationiq_api_key=None,
    )
    geocoder = LocationIQReverseGeocoder(settings)
    result = await geocoder.reverse(latitude=10.8411, longitude=106.8098)
    assert result.nearby_place == "vị trí được chia sẻ"
    assert "10.8411000" in result.map_url


@pytest.mark.asyncio
async def test_locationiq_reverse_geocoder_raises_location_provider_error_on_failure() -> None:
    settings = Settings(
        _env_file=None,
        environment="test",
        locationiq_api_key=SecretStr("locationiq-secret"),
    )
    client_error = FakeHttpClient(FakeResponse({"error": "Internal Error"}, status_code=500))

    with pytest.raises(LocationProviderError):
        await LocationIQReverseGeocoder(settings, client=client_error).reverse(
            latitude=10.0,
            longitude=20.0,
        )


class FailingSmsDelivery:
    async def send_location_alert(self, **kwargs: object) -> str:
        raise SmsProviderError("SpeedSMS network failure")


@pytest.mark.asyncio
async def test_location_alert_service_raises_external_error_on_sms_failure() -> None:
    audit = FakeAudit()
    service = LocationAlertService(
        lookup=FakeLookup(profile()),
        geocoder=FakeGeocoder(),
        sms_delivery=FailingSmsDelivery(),
        audit_repository=audit,
        limiter=LocationAlertLimiter(
            token_cooldown_seconds=300,
            ip_window_seconds=3600,
            ip_burst=5,
        ),
    )

    with pytest.raises(LocationAlertExternalError):
        await service.send(token="public-token", request=request(), client_key="127.0.0.1")

    assert audit.events[0]["status"] == "provider_error"


class FailingGeocoder:
    async def reverse(self, **kwargs: object) -> ReverseGeocodedLocation:
        raise LocationProviderError("LocationIQ unavailable")


@pytest.mark.asyncio
async def test_location_alert_service_falls_back_when_geocoder_raises_error() -> None:
    audit = FakeAudit()
    sms = FakeSmsDelivery()
    service = LocationAlertService(
        lookup=FakeLookup(profile()),
        geocoder=FailingGeocoder(),
        sms_delivery=sms,
        audit_repository=audit,
        limiter=LocationAlertLimiter(
            token_cooldown_seconds=300,
            ip_window_seconds=3600,
            ip_burst=5,
        ),
    )

    result = await service.send(token="public-token", request=request(), client_key="127.0.0.1")
    assert result.status == "sent"
    assert result.nearby_place == "vị trí được chia sẻ"
    assert len(sms.calls) == 1
    assert sms.calls[0]["nearby_place"] == "vị trí được chia sẻ"
    assert audit.events[0]["status"] == "sent"


class PartialFailingSmsDelivery:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def send_location_alert(self, *, recipient_phone: str, **kwargs: object) -> str:
        self.calls.append(recipient_phone)
        if recipient_phone == "0123456789":
            raise SmsProviderError("Invalid recipient phone number")
        return "provider-msg-ok"


@pytest.mark.asyncio
async def test_location_alert_service_continues_when_partial_recipient_fails() -> None:
    audit = FakeAudit()
    sms = PartialFailingSmsDelivery()
    prof = ProfileDocument.model_validate(
        {
            "_id": "507f1f77bcf86cd799439011",
            "user_id": "507f1f77bcf86cd799439012",
            "display_name": "Nguyen Van A",
            "birth_year": 1995,
            "gender": "prefer_not_to_say",
            "blood_type": "O+",
            "critical_allergies": [],
            "important_conditions": [],
            "critical_medications": [],
            "location_alerts_enabled": True,
            "emergency_contacts": [
                {"name": "Bad Contact", "relationship": "Friend", "phone": "0123456789"},
                {"name": "Good Contact", "relationship": "Family", "phone": "0364933038"},
            ],
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        }
    )
    service = LocationAlertService(
        lookup=FakeLookup(prof),
        geocoder=FakeGeocoder(),
        sms_delivery=sms,
        audit_repository=audit,
        limiter=LocationAlertLimiter(
            token_cooldown_seconds=300,
            ip_window_seconds=3600,
            ip_burst=5,
        ),
    )

    result = await service.send(token="public-token", request=request(), client_key="127.0.0.1")
    assert result.status == "sent"
    assert result.provider_id == "provider-msg-ok"
    assert result.recipient_count == 2
    assert sms.calls == ["0123456789", "0364933038"]
    assert audit.events[0]["status"] == "sent"
    assert audit.events[0]["provider_id"] == "provider-msg-ok"


@pytest.mark.asyncio
async def test_location_alert_cooldown_prevents_duplicate_delivery() -> None:
    service, geocoder, sms, _ = service_for(profile())

    first = await service.send(token="public-token", request=request(), client_key="127.0.0.1")
    second = await service.send(token="public-token", request=request(), client_key="127.0.0.1")

    assert first.status == "sent"
    assert second.status == "cooldown"
    assert len(geocoder.calls) == 1
    assert len(sms.calls) == 1


def test_location_alert_route_never_returns_contact_phone_or_email() -> None:
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
    assert "0901234567" not in response.text
    assert "contact@example.com" not in response.text


def test_strip_vietnamese_accents() -> None:
    assert strip_vietnamese_accents("Nguyễn Văn A") == "Nguyen Van A"
    assert strip_vietnamese_accents("Đà Nẵng") == "Da Nang"
    assert strip_vietnamese_accents("Bệnh viện Chợ Rẫy") == "Benh vien Cho Ray"


def test_format_location_alert_sms_fits_single_segment() -> None:
    message = format_location_alert_sms(
        display_name="Nguyễn Văn A",
        nearby_place="Bệnh viện Chợ Rẫy, Phường 12, Quận 5, TP Hồ Chí Minh",
        map_url="https://www.google.com/maps?q=10.7578291,106.6598212",
        max_length=160,
    )
    assert len(message) <= 160
    assert "https://www.google.com/maps?q=" in message
    assert "Nguyen Van A" in message
    assert "[EmerCard]" in message
    assert "..." in message
    assert "\u2026" not in message
    assert all(ord(c) < 128 for c in message)


@pytest.mark.asyncio
async def test_speedsms_delivery_success() -> None:
    settings = Settings(
        environment="test",
        speedsms_access_token=SecretStr("test-token"),
        speedsms_sms_type=2,
    )
    client = FakeHttpClient(
        FakeResponse(
            {
                "status": "success",
                "code": "00",
                "data": {"tranId": 12345678, "totalSMS": 1, "totalPrice": 250, "invalidPhone": []},
            }
        )
    )
    delivery = SpeedSmsDelivery(settings, client=client)  # type: ignore[arg-type]
    tran_id = await delivery.send_location_alert(
        recipient_phone="0912345678",
        display_name="Nguyen Van A",
        nearby_place="Ben Thanh",
        map_url="https://www.google.com/maps?q=10.77,106.69",
        occurred_at=datetime(2026, 1, 1, 10, 30, tzinfo=UTC),
        accuracy_meters=20.0,
    )

    assert tran_id == "12345678"
    assert len(client.post_calls) == 1
    call = client.post_calls[0]
    assert call["url"] == "https://api.speedsms.vn/index.php/sms/send"
    payload = call["json"]
    assert isinstance(payload, dict)
    assert payload["to"] == ["0912345678"]
    assert payload["sms_type"] == 2
    assert len(str(payload["content"])) <= 160


@pytest.mark.asyncio
async def test_speedsms_delivery_handles_api_errors() -> None:
    settings = Settings(
        environment="test",
        speedsms_access_token=SecretStr("test-token"),
        speedsms_sms_type=2,
    )
    error_client = FakeHttpClient(
        FakeResponse({"status": "error", "code": "300", "message": "Balance not enough"})
    )
    delivery = SpeedSmsDelivery(settings, client=error_client)  # type: ignore[arg-type]

    with pytest.raises(SmsProviderError, match="Balance not enough"):
        await delivery.send_location_alert(
            recipient_phone="0912345678",
            display_name="Nguyen Van A",
            nearby_place="Ben Thanh",
            map_url="https://www.google.com/maps?q=10.77,106.69",
            occurred_at=datetime(2026, 1, 1, 10, 30, tzinfo=UTC),
            accuracy_meters=20.0,
        )


@pytest.mark.asyncio
async def test_speedsms_delivery_rejects_invalid_phone() -> None:
    settings = Settings(
        environment="test",
        speedsms_access_token=SecretStr("test-token"),
        speedsms_sms_type=2,
    )
    invalid_phone_client = FakeHttpClient(
        FakeResponse(
            {
                "status": "success",
                "code": "00",
                "data": {"tranId": 12345678, "invalidPhone": ["0912345678"]},
            }
        )
    )
    delivery = SpeedSmsDelivery(settings, client=invalid_phone_client)  # type: ignore[arg-type]

    with pytest.raises(SmsProviderError, match="invalid"):
        await delivery.send_location_alert(
            recipient_phone="0912345678",
            display_name="Nguyen Van A",
            nearby_place="Ben Thanh",
            map_url="https://www.google.com/maps?q=10.77,106.69",
            occurred_at=datetime(2026, 1, 1, 10, 30, tzinfo=UTC),
            accuracy_meters=20.0,
        )


@pytest.mark.asyncio
async def test_android_sms_delivery_success() -> None:
    settings = Settings(
        environment="test",
        sms_provider="android",
        android_sms_gateway_url="http://10.64.235.153:8080",
        android_sms_gateway_username="sms",
        android_sms_gateway_password=SecretStr("test-password"),
    )
    client = FakeHttpClient(
        FakeResponse(
            {
                "id": "Zs7m6R59Eg0AHc5t8jEvz",
                "state": "Pending",
                "recipients": [{"phoneNumber": "0364933038", "state": "Pending"}],
            },
            status_code=202,
        )
    )
    delivery = AndroidSmsDelivery(settings, client=client)  # type: ignore[arg-type]
    message_id = await delivery.send_location_alert(
        recipient_phone="0364933038",
        display_name="Nguyen Van A",
        nearby_place="Ben Thanh",
        map_url="https://www.google.com/maps?q=10.77,106.69",
        occurred_at=datetime(2026, 1, 1, 10, 30, tzinfo=UTC),
        accuracy_meters=20.0,
    )

    assert message_id == "Zs7m6R59Eg0AHc5t8jEvz"
    assert len(client.post_calls) == 1
    call = client.post_calls[0]
    assert call["url"] == "http://10.64.235.153:8080/message"
    payload = call["json"]
    assert isinstance(payload, dict)
    assert payload["phoneNumbers"] == ["+84364933038"]
    assert "textMessage" in payload
    assert payload.get("withDeliveryReport") is True


@pytest.mark.asyncio
async def test_android_sms_delivery_normalizes_cloud_messages_endpoint() -> None:
    settings = Settings(
        environment="test",
        sms_provider="android",
        android_sms_gateway_url="https://api.sms-gate.app/3rdparty/v1/messages",
        android_sms_gateway_username="cloud-user",
        android_sms_gateway_password=SecretStr("cloud-secret"),
    )
    client = FakeHttpClient(
        FakeResponse(
            {"id": "cloud-msg-123", "state": "Pending"},
            status_code=202,
        )
    )
    delivery = AndroidSmsDelivery(settings, client=client)  # type: ignore[arg-type]
    message_id = await delivery.send_location_alert(
        recipient_phone="0364933038",
        display_name="Nguyen Van A",
        nearby_place="Ben Thanh",
        map_url="https://www.google.com/maps?q=10.77,106.69",
        occurred_at=datetime(2026, 1, 1, 10, 30, tzinfo=UTC),
        accuracy_meters=20.0,
    )

    assert message_id == "cloud-msg-123"
    assert len(client.post_calls) == 1
    assert client.post_calls[0]["url"] == "https://api.sms-gate.app/3rdparty/v1/messages"
    payload = client.post_calls[0]["json"]
    assert isinstance(payload, dict)
    assert payload["phoneNumbers"] == ["+84364933038"]
    assert payload.get("withDeliveryReport") is True


@pytest.mark.asyncio
async def test_android_sms_delivery_raises_error_on_failure() -> None:
    settings = Settings(
        environment="test",
        sms_provider="android",
        android_sms_gateway_url="http://10.64.235.153:8080",
        android_sms_gateway_username="sms",
        android_sms_gateway_password=SecretStr("test-password"),
    )
    error_client = FakeHttpClient(FakeResponse({"error": "Unauthorized"}, status_code=401))
    delivery = AndroidSmsDelivery(settings, client=error_client)  # type: ignore[arg-type]

    with pytest.raises(SmsProviderError):
        await delivery.send_location_alert(
            recipient_phone="0364933038",
            display_name="Nguyen Van A",
            nearby_place="Ben Thanh",
            map_url="https://www.google.com/maps?q=10.77,106.69",
            occurred_at=datetime(2026, 1, 1, 10, 30, tzinfo=UTC),
            accuracy_meters=20.0,
        )


@pytest.mark.asyncio
async def test_location_alert_records_recipient_delivery_receipts_in_audit() -> None:
    doc = profile()
    doc.emergency_contacts.append(
        EmergencyContactDocument(
            name="Tran Thi C",
            relationship="Spouse",
            phone="0912345678",
            email="spouse@example.com",
        )
    )
    service, _geocoder, _sms, audit = service_for(doc)

    result = await service.send(token="public-token", request=request(), client_key="127.0.0.1")

    assert result.status == "sent"
    assert len(audit.events) == 1
    event = audit.events[0]
    assert event["latitude"] == 10.772
    assert event["longitude"] == 106.698
    assert event["accuracy_meters"] == 25
    assert len(event["recipients"]) == 2
    assert event["recipients"][0]["recipient_name"] == "Sam Example"
    assert event["recipients"][0]["recipient_phone"] == "090****567"
    assert event["recipients"][0]["status"] == "sent"
    assert event["recipients"][1]["recipient_name"] == "Tran Thi C"
    assert event["recipients"][1]["recipient_phone"] == "091****678"
    assert event["recipients"][1]["status"] == "sent"


class FakeAuditCursor:
    def __init__(self, records: list[dict[str, Any]]) -> None:
        self.records = records

    def sort(self, fields: list[tuple[str, int]]) -> FakeAuditCursor:
        for field, direction in reversed(fields):
            self.records.sort(
                key=lambda record: (record[field], record["_id"]), reverse=direction < 0
            )
        return self

    async def to_list(self, length: int | None = None) -> list[dict[str, Any]]:
        return self.records if length is None else self.records[:length]


class FakeAuditCollection:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    async def insert_one(self, record: dict[str, Any]) -> None:
        self.records.append(record)

    def find(self, query: dict[str, Any]) -> FakeAuditCursor:
        matched: list[dict[str, Any]] = []
        for record in self.records:
            if "card_id" in query and record.get("card_id") != query["card_id"]:
                continue
            if "$or" in query:
                or_matches = False
                for cond in query["$or"]:
                    if (
                        "created_at" in cond
                        and isinstance(cond["created_at"], dict)
                        and "$lt" in cond["created_at"]
                        and not (record["created_at"] < cond["created_at"]["$lt"])
                    ):
                        continue
                    if (
                        "_id" in cond
                        and isinstance(cond["_id"], dict)
                        and "$lt" in cond["_id"]
                        and not (record["_id"] < cond["_id"]["$lt"])
                    ):
                        continue
                    or_matches = True
                    break
                if not or_matches:
                    continue
            matched.append(record)
        return FakeAuditCursor(matched)


class FakeAuditDb:
    def __init__(self) -> None:
        self.collection = FakeAuditCollection()

    def __getitem__(self, name: str) -> FakeAuditCollection:
        return self.collection


@pytest.mark.asyncio
async def test_mongo_location_alert_audit_repository_records_and_pages() -> None:
    settings = Settings(environment="test")
    fake_db = FakeAuditDb()
    repo = MongoLocationAlertAuditRepository(fake_db, settings)

    t1 = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    t2 = datetime(2026, 1, 1, 11, 0, tzinfo=UTC)
    t3 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

    await repo.record(
        link_id="link-1",
        card_id="card-123",
        created_at=t1,
        status="sent",
        nearby_place="Place 1",
        provider_id="p-1",
        location_bucket="10.77,106.69",
        client_key_hash="hash-1",
        latitude=10.771,
        longitude=106.691,
        accuracy_meters=15,
        map_url="https://map1",
        recipients=[
            {
                "recipient_name": "Contact 1",
                "recipient_phone": "090****111",
                "status": "sent",
            }
        ],
    )
    await repo.record(
        link_id="link-1",
        card_id="card-123",
        created_at=t2,
        status="sent",
        nearby_place="Place 2",
        provider_id="p-2",
        location_bucket="10.77,106.69",
        client_key_hash="hash-2",
        latitude=10.772,
        longitude=106.692,
        accuracy_meters=20,
        map_url="https://map2",
        recipients=[
            {
                "recipient_name": "Contact 2",
                "recipient_phone": "090****222",
                "status": "sent",
            }
        ],
    )
    await repo.record(
        link_id="link-1",
        card_id="card-other",
        created_at=t3,
        status="sent",
        nearby_place="Place 3",
        provider_id="p-3",
        location_bucket="10.77,106.69",
        client_key_hash="hash-3",
    )

    page1, cursor1 = await repo.list_by_card_id(card_id="card-123", limit=1)
    assert len(page1) == 1
    assert page1[0]["nearby_place"] == "Place 2"
    assert cursor1 is not None

    page2, cursor2 = await repo.list_by_card_id(card_id="card-123", limit=1, cursor=cursor1)
    assert len(page2) == 1
    assert page2[0]["nearby_place"] == "Place 1"
    assert cursor2 is None

    with pytest.raises(ValueError, match="limit must be between 1 and 100"):
        await repo.list_by_card_id(card_id="card-123", limit=0)

    with pytest.raises(ValueError, match="invalid alert history cursor"):
        await repo.list_by_card_id(card_id="card-123", cursor="invalid!cursor")
