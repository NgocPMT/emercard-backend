from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from bson import ObjectId
from fastapi.testclient import TestClient
from pydantic import SecretStr

from emercard.api.middleware import EmergencyRateLimiter
from emercard.core.config import Settings
from emercard.db.repositories import RepositoryError
from emercard.main import create_app
from emercard.modules.cards import CardDocument, CardRepository, CardStatus, hash_public_token
from emercard.modules.emergency import EmergencyLookupService
from emercard.modules.emergency.errors import (
    EmergencyProfileNotFoundError,
    EmergencyProfileServiceUnavailableError,
)
from emercard.modules.profiles import ProfileDocument
from tests.conftest import FakeDatabase

NOW = datetime(2026, 1, 1, tzinfo=UTC)
OWNER_ID = ObjectId("507f1f77bcf86cd799439011")
TOKEN = "emergency-token_123"


def profile_document() -> ProfileDocument:
    return ProfileDocument.model_validate(
        {
            "_id": ObjectId("507f1f77bcf86cd799439012"),
            "user_id": OWNER_ID,
            "display_name": "Alex Example",
            "birth_year": 1995,
            "gender": "male",
            "blood_type": "O+",
            "critical_allergies": ["Penicillin"],
            "important_conditions": ["Asthma"],
            "critical_medications": ["Salbutamol"],
            "emergency_note": "Use inhaler if breathing is difficult.",
            "emergency_contacts": [
                {
                    "id": "internal-contact-id",
                    "name": "Sam Example",
                    "relationship": "Family",
                    "phone": "0900000000",
                }
            ],
            "public_access": {
                "token": "legacy-secret",
                "enabled": True,
                "published_at": NOW,
            },
            "created_at": NOW,
            "updated_at": NOW,
        }
    )


def active_card() -> CardDocument:
    return CardDocument(
        _id=ObjectId("507f1f77bcf86cd799439013"),
        serial="EMC-0000-0000-0000-0",
        owner_id=OWNER_ID,
        token_hash=hash_public_token(TOKEN),
        token_revision=1,
        provisioned_at=NOW,
        encoding_verified_at=NOW,
        encoded_by_admin_id=ObjectId("507f1f77bcf86cd799439014"),
        assigned_at=NOW,
        activated_at=NOW,
        issued_at=NOW,
        issued_by_admin_id=ObjectId("507f1f77bcf86cd799439014"),
        status=CardStatus.ACTIVE,
        is_current=True,
        created_at=NOW,
        updated_at=NOW,
    )


class FakeCardRepository:
    def __init__(self, card: CardDocument | None) -> None:
        self.card = card
        self.calls: list[str] = []

    async def find_publicly_resolvable_by_token_hash(self, token_hash: str) -> CardDocument | None:
        self.calls.append(token_hash)
        if self.card is not None and self.card.token_hash == token_hash:
            return self.card
        return None

    async def find_by_token_hash(self, token_hash: str) -> CardDocument | None:
        raise AssertionError(f"generic token lookup must not be used: {token_hash}")


class FakeProfileRepository:
    def __init__(self, profile: ProfileDocument | None) -> None:
        self.profile = profile
        self.failure: Exception | None = None
        self.calls: list[str] = []

    async def find_by_user_id(self, user_id: str) -> ProfileDocument | None:
        self.calls.append(user_id)
        if self.failure is not None:
            raise self.failure
        if self.profile is not None and str(self.profile.user_id) == user_id:
            return self.profile
        return None


def settings(**overrides: object) -> Settings:
    return Settings(
        environment="test",
        auth_secret=SecretStr("test-auth-secret-012345678901234567890"),
        cors_origins=["http://localhost:4321"],
        **overrides,
    )


def make_client(
    *,
    card: CardDocument | None,
    profile: ProfileDocument | None,
    rate_limiter: EmergencyRateLimiter | None = None,
) -> tuple[TestClient, FakeCardRepository, FakeProfileRepository]:
    card_repository = FakeCardRepository(card)
    profile_repository = FakeProfileRepository(profile)
    app = create_app(
        settings=settings(),
        database=FakeDatabase(ready=True),
        card_repository=card_repository,
        profile_repository=profile_repository,
        emergency_rate_limiter=rate_limiter,
    )
    return TestClient(app), card_repository, profile_repository


@pytest.mark.asyncio
async def test_lookup_hashes_raw_token_and_uses_only_constrained_repository_method() -> None:
    cards = FakeCardRepository(active_card())
    profiles = FakeProfileRepository(profile_document())

    result = await EmergencyLookupService(cards, profiles).lookup(TOKEN)

    assert result.display_name == "Alex Example"
    assert cards.calls == [hash_public_token(TOKEN)]
    assert profiles.calls == [str(OWNER_ID)]
    assert "legacy-secret" not in result.model_dump_json()
    assert "internal-contact-id" not in result.model_dump_json()


@pytest.mark.asyncio
@pytest.mark.parametrize("token", ["", "contains space", "contains/slash", "x" * 129])
async def test_lookup_treats_malformed_tokens_as_neutral_not_found(token: str) -> None:
    cards = MagicMock()
    profiles = MagicMock()

    with pytest.raises(EmergencyProfileNotFoundError):
        await EmergencyLookupService(cards, profiles).lookup(token)

    cards.find_publicly_resolvable_by_token_hash.assert_not_called()


@pytest.mark.asyncio
async def test_lookup_maps_dependency_failure_without_exposing_details() -> None:
    cards = FakeCardRepository(active_card())
    profiles = FakeProfileRepository(profile_document())
    profiles.failure = RepositoryError("mongodb secret details")

    with pytest.raises(EmergencyProfileServiceUnavailableError) as error:
        await EmergencyLookupService(cards, profiles).lookup(TOKEN)

    assert "mongodb secret details" not in str(error.value)


@pytest.mark.asyncio
async def test_lookup_maps_missing_profile_neutrally() -> None:
    service = EmergencyLookupService(FakeCardRepository(active_card()), FakeProfileRepository(None))
    with pytest.raises(EmergencyProfileNotFoundError):
        await service.lookup(TOKEN)


@pytest.mark.asyncio
async def test_card_repository_enforces_public_eligibility_in_query() -> None:
    database = MagicMock()
    collection = MagicMock()
    collection.find_one = AsyncMock(
        return_value=active_card().model_dump(by_alias=True, mode="python")
    )
    database.__getitem__.return_value = collection
    repository = CardRepository(database, settings())

    result = await repository.find_publicly_resolvable_by_token_hash(hash_public_token(TOKEN))

    assert result is not None
    assert collection.find_one.await_args.args[0] == {
        "token_hash": hash_public_token(TOKEN),
        "status": CardStatus.ACTIVE,
        "is_current": True,
        "owner_id": {"$type": "objectId"},
        "issued_at": {"$type": "date"},
        "encoding_verified_at": {"$type": "date"},
    }


def test_rate_limiter_expires_and_prunes_old_client_buckets() -> None:
    limiter = EmergencyRateLimiter(window_seconds=10, burst=1)

    assert limiter.allow("first", now=0)
    assert not limiter.allow("first", now=1)
    assert limiter.allow("second", now=11)
    assert limiter.allow("first", now=11)


def test_public_lookup_returns_allowlisted_profile_and_privacy_headers() -> None:
    client, _, _ = make_client(card=active_card(), profile=profile_document())
    with client:
        response = client.get(f"/api/v1/emergency/{TOKEN}")

    assert response.status_code == 200
    body = response.json()
    assert body["profile"]["display_name"] == "Alex Example"
    assert body["profile"]["profile_updated_at"] == "2026-01-01T00:00:00Z"
    assert "legacy-secret" not in response.text
    assert "internal-contact-id" not in response.text
    assert "user_id" not in body["profile"]
    assert "state" not in body["profile"]
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"
    assert response.headers["x-robots-tag"] == "noindex, nofollow, noarchive"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_authenticated_browser_state_does_not_change_public_projection() -> None:
    client, _, _ = make_client(card=active_card(), profile=profile_document())
    with client:
        anonymous = client.get(f"/api/v1/emergency/{TOKEN}")
        with_cookie = client.get(
            f"/api/v1/emergency/{TOKEN}", headers={"Cookie": "emercard_session=not-used"}
        )

    assert anonymous.status_code == 200
    assert with_cookie.status_code == 200
    assert anonymous.json() == with_cookie.json()


def test_unavailable_lookup_is_neutral_and_keeps_privacy_headers() -> None:
    client, _, _ = make_client(card=None, profile=None)
    with client:
        response = client.get(f"/api/v1/emergency/{TOKEN}")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "emergency_profile.not_found"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-robots-tag"] == "noindex, nofollow, noarchive"


def test_lookup_dependency_failure_returns_safe_503() -> None:
    client, _, profiles = make_client(card=active_card(), profile=profile_document())
    profiles.failure = RepositoryError("database secret")
    with client:
        response = client.get(f"/api/v1/emergency/{TOKEN}")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "emergency_profile.service_unavailable"
    assert "database secret" not in response.text
    assert response.headers["cache-control"] == "no-store"


def test_rate_limit_is_neutral_and_uses_direct_peer_only() -> None:
    client, _, _ = make_client(
        card=None,
        profile=None,
        rate_limiter=EmergencyRateLimiter(window_seconds=60, burst=1),
    )
    with client:
        first = client.get(f"/api/v1/emergency/{TOKEN}", headers={"X-Forwarded-For": "203.0.113.9"})
        second = client.get(
            f"/api/v1/emergency/{TOKEN}", headers={"X-Forwarded-For": "203.0.113.10"}
        )

    assert first.status_code == 404
    assert second.status_code == 429
    assert second.json()["error"]["code"] == "rate_limit.exceeded"
    assert TOKEN not in second.text
    assert second.headers["cache-control"] == "no-store"
    assert second.headers["retry-after"] == "60"


def test_emergency_request_log_uses_route_template(caplog: pytest.LogCaptureFixture) -> None:
    client, _, _ = make_client(card=None, profile=None)
    with client:
        client.get(f"/api/v1/emergency/{TOKEN}")

    records = [record for record in caplog.records if record.name == "emercard.request"]
    assert records
    assert records[-1].route == "/api/v1/emergency/{token}"
    assert TOKEN not in caplog.text
