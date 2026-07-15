from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from bson import ObjectId
from fastapi.testclient import TestClient
from pydantic import SecretStr

from emercard.api.middleware import EmergencyRateLimiter
from emercard.core.config import Settings
from emercard.db.repositories import RepositoryError
from emercard.main import create_app
from emercard.modules.card_link_assignments import (
    CardLinkAssignmentDocument,
    CardLinkAssignmentStatus,
)
from emercard.modules.cards import hash_public_token
from emercard.modules.emergency import EmergencyLookupService
from emercard.modules.emergency.errors import (
    EmergencyProfileNotFoundError,
    EmergencyProfileServiceUnavailableError,
)
from emercard.modules.profiles import ProfileDocument
from emercard.modules.public_links import (
    PublicAccessLinkDocument,
    PublicAccessLinkStatus,
    PublicLinkPurpose,
)
from tests.conftest import FakeDatabase

NOW = datetime(2026, 1, 1, tzinfo=UTC)
OWNER_ID = ObjectId("507f1f77bcf86cd799439011")
PROFILE_ID = ObjectId("507f1f77bcf86cd799439012")
TOKEN = "emergency-token_123"


def profile_document() -> ProfileDocument:
    return ProfileDocument.model_validate(
        {
            "_id": PROFILE_ID,
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


def active_link(
    *, status: PublicAccessLinkStatus = PublicAccessLinkStatus.ACTIVE
) -> PublicAccessLinkDocument:
    return PublicAccessLinkDocument.model_validate(
        {
            "_id": ObjectId("507f1f77bcf86cd799439013"),
            "profile_id": PROFILE_ID,
            "purpose": PublicLinkPurpose.CARD,
            "label": "Card access",
            "token_hash": hash_public_token(TOKEN),
            "status": status,
            "created_by": ObjectId("507f1f77bcf86cd799439014"),
            "created_at": NOW,
            "updated_at": NOW,
            "activated_at": NOW if status is PublicAccessLinkStatus.ACTIVE else None,
            "disabled_at": NOW if status is PublicAccessLinkStatus.DISABLED else None,
            "revoked_at": NOW if status is PublicAccessLinkStatus.REVOKED else None,
            "expires_at": NOW if status is PublicAccessLinkStatus.EXPIRED else None,
            "expired_at": NOW if status is PublicAccessLinkStatus.EXPIRED else None,
        }
    )


def active_assignment(link: PublicAccessLinkDocument) -> CardLinkAssignmentDocument:
    return CardLinkAssignmentDocument.model_validate(
        {
            "_id": ObjectId("507f1f77bcf86cd799439015"),
            "card_id": ObjectId("507f1f77bcf86cd799439016"),
            "public_access_link_id": link.id,
            "status": CardLinkAssignmentStatus.ACTIVE,
            "attached_at": NOW,
            "updated_at": NOW,
            "attached_by_admin_id": ObjectId("507f1f77bcf86cd799439017"),
            "disabled_at": None,
            "disabled_by_admin_id": None,
            "detached_at": None,
            "detached_by_admin_id": None,
            "detach_reason": None,
        }
    )


class FakeLinkRepository:
    def __init__(self, link: PublicAccessLinkDocument | None) -> None:
        self.link = link
        self.calls: list[str] = []

    async def find_by_token_hash(self, token_hash: str) -> PublicAccessLinkDocument | None:
        self.calls.append(token_hash)
        if self.link is not None and self.link.token_hash == token_hash:
            return self.link
        return None


class FakeProfileRepository:
    def __init__(self, profile: ProfileDocument | None) -> None:
        self.profile = profile
        self.failure: Exception | None = None
        self.calls: list[str] = []

    async def find_by_id(self, profile_id: ObjectId | str) -> ProfileDocument | None:
        self.calls.append(str(profile_id))
        if self.failure is not None:
            raise self.failure
        if self.profile is not None and str(self.profile.id) == str(profile_id):
            return self.profile
        return None


def settings(**overrides: object) -> Settings:
    return Settings(
        environment="test",
        auth_secret=SecretStr("test-auth-secret-012345678901234567890"),
        cors_origins=["http://localhost:4321"],
        **overrides,
    )


class FakeAssignmentRepository:
    def __init__(self, assignment: CardLinkAssignmentDocument | None) -> None:
        self.assignment = assignment
        self.calls: list[str] = []

    async def find_active_by_public_access_link_id(
        self, public_access_link_id: ObjectId | str, *, session: object | None = None
    ) -> CardLinkAssignmentDocument | None:
        del session
        self.calls.append(str(public_access_link_id))
        if self.assignment is not None and str(self.assignment.public_access_link_id) == str(
            public_access_link_id
        ):
            return self.assignment
        return None


def make_client(
    *,
    link: PublicAccessLinkDocument | None,
    profile: ProfileDocument | None,
    rate_limiter: EmergencyRateLimiter | None = None,
) -> tuple[TestClient, FakeLinkRepository, FakeProfileRepository]:
    link_repository = FakeLinkRepository(link)
    profile_repository = FakeProfileRepository(profile)
    app = create_app(
        settings=settings(),
        database=FakeDatabase(ready=True),
        public_access_link_repository=link_repository,
        profile_repository=profile_repository,
        card_link_assignment_repository=FakeAssignmentRepository(
            active_assignment(link) if link is not None else None
        ),
        emergency_rate_limiter=rate_limiter,
    )
    return TestClient(app), link_repository, profile_repository


@pytest.mark.asyncio
async def test_lookup_hashes_raw_token_and_uses_only_constrained_repository_method() -> None:
    links = FakeLinkRepository(active_link())
    profiles = FakeProfileRepository(profile_document())

    result = await EmergencyLookupService(
        links,
        profiles,
        assignment_repository=FakeAssignmentRepository(active_assignment(active_link())),
    ).lookup(TOKEN)

    assert result.display_name == "Alex Example"
    assert links.calls == [hash_public_token(TOKEN)]
    assert profiles.calls == [str(PROFILE_ID)]
    assert "legacy-secret" not in result.model_dump_json()
    assert "internal-contact-id" not in result.model_dump_json()


@pytest.mark.asyncio
async def test_lookup_includes_safe_card_attribution_when_available() -> None:
    link = active_link()
    assignment = active_assignment(link)
    result = await EmergencyLookupService(
        FakeLinkRepository(link),
        FakeProfileRepository(profile_document()),
        assignment_repository=FakeAssignmentRepository(assignment),
    ).lookup(TOKEN)

    assert result.display_name == "Alex Example"
    assert result.link_id == str(link.id)
    assert result.purpose is PublicLinkPurpose.CARD
    assert result.assignment_id == str(assignment.id)
    assert result.card_id == str(assignment.card_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("token", ["", "contains space", "contains/slash", "x" * 129])
async def test_lookup_treats_malformed_tokens_as_neutral_not_found(token: str) -> None:
    links = MagicMock()
    profiles = MagicMock()

    with pytest.raises(EmergencyProfileNotFoundError):
        await EmergencyLookupService(links, profiles).lookup(token)

    links.find_by_token_hash.assert_not_called()


@pytest.mark.asyncio
async def test_lookup_maps_dependency_failure_without_exposing_details() -> None:
    links = FakeLinkRepository(active_link())
    profiles = FakeProfileRepository(profile_document())
    profiles.failure = RepositoryError("mongodb secret details")

    with pytest.raises(EmergencyProfileServiceUnavailableError) as error:
        await EmergencyLookupService(links, profiles).lookup(TOKEN)

    assert "mongodb secret details" not in str(error.value)


@pytest.mark.asyncio
async def test_lookup_maps_missing_profile_neutrally() -> None:
    service = EmergencyLookupService(FakeLinkRepository(active_link()), FakeProfileRepository(None))
    with pytest.raises(EmergencyProfileNotFoundError):
        await service.lookup(TOKEN)


@pytest.mark.asyncio
async def test_lookup_rejects_disabled_public_links_neutrally() -> None:
    service = EmergencyLookupService(
        FakeLinkRepository(active_link(status=PublicAccessLinkStatus.DISABLED)),
        FakeProfileRepository(profile_document()),
    )
    with pytest.raises(EmergencyProfileNotFoundError):
        await service.lookup(TOKEN)


def test_rate_limiter_expires_and_prunes_old_client_buckets() -> None:
    limiter = EmergencyRateLimiter(window_seconds=10, burst=1)

    assert limiter.allow("first", now=0)
    assert not limiter.allow("first", now=1)
    assert limiter.allow("second", now=11)
    assert limiter.allow("first", now=11)


def test_public_lookup_returns_allowlisted_profile_and_privacy_headers() -> None:
    client, _, _ = make_client(link=active_link(), profile=profile_document())
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
    client, _, _ = make_client(link=active_link(), profile=profile_document())
    with client:
        anonymous = client.get(f"/api/v1/emergency/{TOKEN}")
        with_cookie = client.get(
            f"/api/v1/emergency/{TOKEN}", headers={"Cookie": "emercard_session=not-used"}
        )

    assert anonymous.status_code == 200
    assert with_cookie.status_code == 200
    assert anonymous.json() == with_cookie.json()


def test_unavailable_lookup_is_neutral_and_keeps_privacy_headers() -> None:
    client, _, _ = make_client(link=None, profile=None)
    with client:
        response = client.get(f"/api/v1/emergency/{TOKEN}")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "emergency_profile.not_found"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-robots-tag"] == "noindex, nofollow, noarchive"


def test_lookup_dependency_failure_returns_safe_503() -> None:
    client, _, profiles = make_client(link=active_link(), profile=profile_document())
    profiles.failure = RepositoryError("database secret")
    with client:
        response = client.get(f"/api/v1/emergency/{TOKEN}")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "emergency_profile.service_unavailable"
    assert "database secret" not in response.text
    assert response.headers["cache-control"] == "no-store"


def test_rate_limit_is_neutral_and_uses_direct_peer_only() -> None:
    client, _, _ = make_client(
        link=None,
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
    client, _, _ = make_client(link=None, profile=None)
    with client:
        client.get(f"/api/v1/emergency/{TOKEN}")

    records = [record for record in caplog.records if record.name == "emercard.request"]
    assert records
    assert records[-1].route == "/api/v1/emergency/{token}"
    assert TOKEN not in caplog.text
