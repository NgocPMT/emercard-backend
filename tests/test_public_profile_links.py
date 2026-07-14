from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock

import pytest
from bson import ObjectId
from fastapi.testclient import TestClient
from pydantic import SecretStr

from emercard.api.auth_routes import get_current_user
from emercard.core.config import Settings
from emercard.db import public_profile_links as public_profile_links_module
from emercard.db.repositories import RepositoryConflictError, RepositoryError
from emercard.main import create_app
from emercard.modules.card_link_assignments import (
    CardLinkAssignmentDocument,
    CardLinkAssignmentStatus,
)
from emercard.modules.cards import hash_public_token
from emercard.modules.profiles import ProfileDocument
from emercard.modules.public_links import (
    PublicAccessLinkDocument,
    PublicAccessLinkStatus,
    PublicLinkPurpose,
    PublicProfileLinkResult,
    PublicProfileLinkService,
    PublicProfileLookupService,
    PublicProfileNotFoundError,
    PublicProfileNotReadyError,
    PublicProfileServiceUnavailableError,
)
from emercard.modules.users import CurrentUserOutput
from tests.conftest import FakeDatabase

NOW = datetime(2026, 1, 1, tzinfo=UTC)
TOKEN = "public-demo-token_123"
PROFILE_ID = ObjectId("507f1f77bcf86cd799439012")
OWNER_ID = ObjectId("507f1f77bcf86cd799439011")


def settings(**overrides: object) -> Settings:
    return Settings(
        environment="test",
        auth_secret=SecretStr("test-auth-secret-012345678901234567890"),
        cors_origins=["http://localhost:4321"],
        public_profile_base_url="https://app.example/e",
        **overrides,
    )


def ready_profile(*, blood_type: str = "O+", updated_at: datetime = NOW) -> ProfileDocument:
    return ProfileDocument.model_validate(
        {
            "_id": PROFILE_ID,
            "user_id": OWNER_ID,
            "display_name": "Alex Example",
            "birth_year": 1995,
            "gender": "male",
            "blood_type": blood_type,
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
            "updated_at": updated_at,
        }
    )


def incomplete_profile() -> ProfileDocument:
    profile = ready_profile()
    return ProfileDocument.model_validate(
        {
            **profile.model_dump(mode="python", by_alias=True),
            "blood_type": None,
            "updated_at": NOW,
        }
    )


def link_document(
    *,
    token_hash: str,
    status: PublicAccessLinkStatus = PublicAccessLinkStatus.ACTIVE,
    purpose: PublicLinkPurpose = PublicLinkPurpose.STANDALONE,
    label: str | None = None,
) -> PublicAccessLinkDocument:
    payload: dict[str, Any] = {
        "_id": ObjectId("507f1f77bcf86cd799439013"),
        "profile_id": PROFILE_ID,
        "purpose": purpose,
        "label": label,
        "token_hash": token_hash,
        "status": status,
        "created_at": NOW,
        "updated_at": NOW,
        "activated_at": NOW if status is PublicAccessLinkStatus.ACTIVE else None,
        "disabled_at": None if status is PublicAccessLinkStatus.ACTIVE else NOW,
        "revoked_at": None,
        "expires_at": None,
        "expired_at": None,
    }
    return PublicAccessLinkDocument.model_validate(payload)


class InMemoryLinkRepository:
    def __init__(self, link: PublicAccessLinkDocument | None = None) -> None:
        self.links: list[PublicAccessLinkDocument] = [link] if link is not None else []
        self.link = link
        self.by_token_calls: list[str] = []
        self.by_profile_calls: list[str] = []
        self.by_profile_purpose_calls: list[tuple[str, str]] = []
        self.find_by_id_calls: list[str] = []
        self.create_calls: list[tuple[str, str, str]] = []
        self.rotate_calls: list[tuple[str, str]] = []
        self.activate_calls: list[str] = []
        self.disable_calls: list[str] = []
        self.revoke_calls: list[str] = []

    def _replace_link(self, updated: PublicAccessLinkDocument) -> PublicAccessLinkDocument:
        self.link = updated
        replaced = False
        for index, existing in enumerate(self.links):
            if existing.id == updated.id:
                self.links[index] = updated
                replaced = True
                break
        if not replaced:
            self.links.append(updated)
        return updated

    def _match_profile(
        self, profile_id: ObjectId | str, purpose: PublicLinkPurpose | None = None
    ) -> list[PublicAccessLinkDocument]:
        matches = [item for item in self.links if str(item.profile_id) == str(profile_id)]
        if purpose is not None:
            matches = [item for item in matches if item.purpose == purpose]
        return matches

    async def find_by_id(
        self, link_id: ObjectId | str, *, session: object | None = None
    ) -> PublicAccessLinkDocument | None:
        self.find_by_id_calls.append(str(link_id))
        for link in self.links:
            if str(link.id) == str(link_id):
                return link
        return None

    async def list_by_profile_id(
        self,
        profile_id: ObjectId | str,
        *,
        purpose: PublicLinkPurpose | None = None,
        session: object | None = None,
    ) -> list[PublicAccessLinkDocument]:
        self.by_profile_calls.append(str(profile_id))
        if purpose is not None:
            self.by_profile_purpose_calls.append((str(profile_id), purpose))
        return sorted(
            self._match_profile(profile_id, purpose=purpose),
            key=lambda item: (item.created_at, item.id),
            reverse=True,
        )

    async def find_by_profile_id_and_purpose(
        self,
        profile_id: ObjectId | str,
        *,
        purpose: PublicLinkPurpose,
        session: object | None = None,
    ) -> PublicAccessLinkDocument | None:
        self.by_profile_purpose_calls.append((str(profile_id), purpose))
        matches = self._match_profile(profile_id, purpose=purpose)
        return matches[0] if matches else None

    async def find_by_token_hash(
        self, token_hash: str, *, session: object | None = None
    ) -> PublicAccessLinkDocument | None:
        self.by_token_calls.append(token_hash)
        for link in self.links:
            if link.token_hash == token_hash:
                return link
        return None

    async def find_active_by_token_hash(
        self, token_hash: str, *, session: object | None = None
    ) -> PublicAccessLinkDocument | None:
        link = await self.find_by_token_hash(token_hash, session=session)
        if link is not None and link.status is PublicAccessLinkStatus.ACTIVE:
            return link
        return None

    async def create_link(
        self,
        *,
        profile_id: ObjectId | str,
        purpose: PublicLinkPurpose,
        token_hash: str,
        label: str | None = None,
        status: PublicAccessLinkStatus = PublicAccessLinkStatus.ACTIVE,
        created_by: ObjectId | str | None = None,
        now: object | None = None,
        session: object | None = None,
    ) -> PublicAccessLinkDocument:
        self.create_calls.append((str(profile_id), purpose, token_hash))
        created = PublicAccessLinkDocument.model_validate(
            {
                "_id": ObjectId(),
                "profile_id": ObjectId(str(profile_id)),
                "purpose": purpose,
                "label": label,
                "token_hash": token_hash,
                "status": status,
                "created_by": created_by,
                "created_at": now or NOW,
                "updated_at": now or NOW,
                "activated_at": now or NOW if status is PublicAccessLinkStatus.ACTIVE else None,
                "disabled_at": None if status is PublicAccessLinkStatus.ACTIVE else now or NOW,
                "revoked_at": None,
                "expires_at": None,
                "expired_at": None,
            }
        )
        return self._replace_link(created)

    async def rotate_link(
        self,
        *,
        link_id: ObjectId | str,
        token_hash: str,
        now: object | None = None,
        session: object | None = None,
    ) -> PublicAccessLinkDocument:
        self.rotate_calls.append((str(link_id), token_hash))
        existing = await self.find_by_id(link_id)
        created_at = existing.created_at if existing is not None else (now or NOW)
        purpose = existing.purpose if existing is not None else PublicLinkPurpose.STANDALONE
        rotated = PublicAccessLinkDocument.model_validate(
            {
                "_id": existing.id if existing is not None else ObjectId(),
                "profile_id": existing.profile_id if existing is not None else PROFILE_ID,
                "purpose": purpose,
                "label": existing.label if existing is not None else None,
                "token_hash": token_hash,
                "status": PublicAccessLinkStatus.ACTIVE,
                "created_by": existing.created_by if existing is not None else None,
                "created_at": created_at,
                "updated_at": now or NOW,
                "activated_at": now or NOW,
                "disabled_at": None,
                "revoked_at": None,
                "expires_at": None,
                "expired_at": None,
            }
        )
        return self._replace_link(rotated)

    async def activate_link(
        self,
        *,
        link_id: ObjectId | str,
        now: object | None = None,
        session: object | None = None,
    ) -> PublicAccessLinkDocument | None:
        self.activate_calls.append(str(link_id))
        existing = await self.find_by_id(link_id)
        if existing is None:
            return None
        activated = PublicAccessLinkDocument.model_validate(
            {
                **existing.model_dump(mode="python", by_alias=True),
                "status": PublicAccessLinkStatus.ACTIVE,
                "updated_at": now or NOW,
                "activated_at": now or NOW,
                "disabled_at": None,
                "revoked_at": None,
                "expires_at": None,
                "expired_at": None,
            }
        )
        return self._replace_link(activated)

    async def disable_link(
        self,
        *,
        link_id: ObjectId | str,
        now: object | None = None,
        session: object | None = None,
    ) -> PublicAccessLinkDocument | None:
        self.disable_calls.append(str(link_id))
        existing = await self.find_by_id(link_id)
        if existing is None:
            return None
        disabled = PublicAccessLinkDocument.model_validate(
            {
                **existing.model_dump(mode="python", by_alias=True),
                "status": PublicAccessLinkStatus.DISABLED,
                "updated_at": now or NOW,
                "disabled_at": now or NOW,
            }
        )
        return self._replace_link(disabled)

    async def revoke_link(
        self,
        *,
        link_id: ObjectId | str,
        now: object | None = None,
        session: object | None = None,
    ) -> PublicAccessLinkDocument | None:
        self.revoke_calls.append(str(link_id))
        existing = await self.find_by_id(link_id)
        if existing is None:
            return None
        revoked = PublicAccessLinkDocument.model_validate(
            {
                **existing.model_dump(mode="python", by_alias=True),
                "status": PublicAccessLinkStatus.REVOKED,
                "updated_at": now or NOW,
                "revoked_at": now or NOW,
            }
        )
        return self._replace_link(revoked)

    async def create_for_profile(
        self,
        *,
        profile_id: ObjectId | str,
        token_hash: str,
        now: object | None = None,
        session: object | None = None,
    ) -> PublicAccessLinkDocument:
        return await self.create_link(
            profile_id=profile_id,
            purpose=PublicLinkPurpose.STANDALONE,
            token_hash=token_hash,
            now=now,
            session=session,
        )

    async def rotate_for_profile(
        self,
        *,
        profile_id: ObjectId | str,
        token_hash: str,
        now: object | None = None,
        session: object | None = None,
    ) -> PublicAccessLinkDocument:
        active = await self.find_by_profile_id_and_purpose(
            profile_id, purpose=PublicLinkPurpose.STANDALONE, session=session
        )
        if active is None:
            return await self.create_for_profile(
                profile_id=profile_id, token_hash=token_hash, now=now, session=session
            )
        return await self.rotate_link(
            link_id=active.id, token_hash=token_hash, now=now, session=session
        )

    async def disable_for_profile(
        self,
        *,
        profile_id: ObjectId | str,
        now: object | None = None,
        session: object | None = None,
    ) -> PublicAccessLinkDocument | None:
        active = await self.find_by_profile_id_and_purpose(
            profile_id, purpose=PublicLinkPurpose.STANDALONE, session=session
        )
        if active is None:
            return None
        return await self.disable_link(link_id=active.id, now=now, session=session)


class InMemoryProfileRepository:
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

    async def find_by_user_id(self, user_id: ObjectId | str) -> ProfileDocument | None:
        self.calls.append(str(user_id))
        if self.failure is not None:
            raise self.failure
        if self.profile is not None and str(self.profile.user_id) == str(user_id):
            return self.profile
        return None


class FakePublicLinkRepository:
    def __init__(self, link: PublicAccessLinkDocument | None) -> None:
        self.link = link
        self.calls: list[str] = []

    async def find_by_token_hash(self, token_hash: str) -> PublicAccessLinkDocument | None:
        self.calls.append(token_hash)
        if self.link is not None and self.link.token_hash == token_hash:
            return self.link
        return None


class FailingPublicLinkRepository(FakePublicLinkRepository):
    async def find_by_token_hash(self, token_hash: str) -> PublicAccessLinkDocument | None:
        raise RepositoryError("link store unavailable")


class InMemoryAssignmentRepository:
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


class FlakyLifecycleLinkRepository(InMemoryLinkRepository):
    def __init__(self, link: PublicAccessLinkDocument | None = None) -> None:
        super().__init__(link)
        self.create_failures = 1
        self.rotate_failures = 1

    async def create_link(
        self,
        *,
        profile_id: ObjectId | str,
        purpose: PublicLinkPurpose,
        token_hash: str,
        label: str | None = None,
        status: PublicAccessLinkStatus = PublicAccessLinkStatus.ACTIVE,
        created_by: ObjectId | str | None = None,
        now: object | None = None,
        session: object | None = None,
    ) -> PublicAccessLinkDocument:
        if self.create_failures > 0:
            self.create_failures -= 1
            raise RepositoryConflictError("collision")
        return await super().create_link(
            profile_id=profile_id,
            purpose=purpose,
            token_hash=token_hash,
            label=label,
            status=status,
            created_by=created_by,
            now=now,
            session=session,
        )

    async def rotate_link(
        self,
        *,
        link_id: ObjectId | str,
        token_hash: str,
        now: object | None = None,
        session: object | None = None,
    ) -> PublicAccessLinkDocument:
        if self.rotate_failures > 0:
            self.rotate_failures -= 1
            raise RepositoryConflictError("collision")
        return await super().rotate_link(
            link_id=link_id, token_hash=token_hash, now=now, session=session
        )


def make_client(
    *,
    link: PublicAccessLinkDocument | None,
    profile: ProfileDocument | None,
) -> tuple[TestClient, FakePublicLinkRepository, InMemoryProfileRepository]:
    link_repository = FakePublicLinkRepository(link)
    profile_repository = InMemoryProfileRepository(profile)
    app = create_app(
        settings=settings(),
        database=FakeDatabase(ready=True),
        public_access_link_repository=link_repository,
        profile_repository=profile_repository,
    )
    return TestClient(app), link_repository, profile_repository


@pytest.mark.asyncio
async def test_public_lookup_returns_allowlisted_public_profile() -> None:
    service = PublicProfileLookupService(
        InMemoryLinkRepository(link_document(token_hash=hash_public_token(TOKEN))),
        InMemoryProfileRepository(ready_profile()),
    )

    result = await service.lookup(TOKEN)

    assert result.display_name == "Alex Example"
    assert result.profile_updated_at == NOW
    assert "legacy-secret" not in result.model_dump_json()
    assert "internal-contact-id" not in result.model_dump_json()


@pytest.mark.asyncio
@pytest.mark.parametrize("token", ["", "contains space", "contains/slash", "x" * 129])
async def test_public_lookup_treats_malformed_tokens_as_not_found(token: str) -> None:
    links = MagicMock()
    profiles = MagicMock()

    with pytest.raises(PublicProfileNotFoundError):
        await PublicProfileLookupService(links, profiles).lookup(token)

    links.find_by_token_hash.assert_not_called()


@pytest.mark.asyncio
async def test_public_lookup_rejects_disabled_links() -> None:
    service = PublicProfileLookupService(
        InMemoryLinkRepository(
            link_document(
                token_hash=hash_public_token(TOKEN), status=PublicAccessLinkStatus.DISABLED
            )
        ),
        InMemoryProfileRepository(ready_profile()),
    )

    with pytest.raises(PublicProfileNotFoundError):
        await service.lookup(TOKEN)


@pytest.mark.asyncio
async def test_public_lookup_returns_profiles_even_when_incomplete() -> None:
    service = PublicProfileLookupService(
        InMemoryLinkRepository(link_document(token_hash=hash_public_token(TOKEN))),
        InMemoryProfileRepository(incomplete_profile()),
    )

    result = await service.lookup(TOKEN)

    assert result.display_name == "Alex Example"
    assert result.blood_type is None


@pytest.mark.asyncio
async def test_public_lookup_maps_dependency_failure_without_details() -> None:
    service = PublicProfileLookupService(
        FailingPublicLinkRepository(link_document(token_hash=hash_public_token(TOKEN))),
        InMemoryProfileRepository(ready_profile()),
    )

    with pytest.raises(PublicProfileServiceUnavailableError) as error:
        await service.lookup(TOKEN)

    assert "unavailable" not in str(error.value).lower()


def test_public_profile_route_returns_privacy_headers_and_same_projection(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client, _, _ = make_client(
        link=link_document(token_hash=hash_public_token(TOKEN)), profile=ready_profile()
    )
    caplog.set_level(logging.INFO, logger="emercard.request")
    with client:
        anonymous = client.get(f"/api/v1/public/{TOKEN}")
        with_cookie = client.get(
            f"/api/v1/public/{TOKEN}", headers={"Cookie": "emercard_session=not-used"}
        )

    assert anonymous.status_code == 200
    assert with_cookie.status_code == 200
    assert anonymous.json() == with_cookie.json()
    assert anonymous.headers["cache-control"] == "no-store"
    assert anonymous.headers["x-robots-tag"] == "noindex, nofollow, noarchive"
    assert anonymous.headers["referrer-policy"] == "no-referrer"
    assert anonymous.headers["x-content-type-options"] == "nosniff"
    assert any(
        getattr(record, "route", None) == "/api/v1/public/{token}" for record in caplog.records
    )
    assert all(TOKEN not in record.getMessage() for record in caplog.records)


def test_public_profile_route_returns_not_found_for_unknown_tokens() -> None:
    client, _, _ = make_client(link=None, profile=ready_profile())
    with client:
        response = client.get(f"/api/v1/public/{TOKEN}")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "public_profile.not_found"
    assert response.headers["cache-control"] == "no-store"


def test_public_profile_route_returns_disabled_for_disabled_links() -> None:
    client, _, _ = make_client(
        link=link_document(
            token_hash=hash_public_token(TOKEN), status=PublicAccessLinkStatus.DISABLED
        ),
        profile=ready_profile(),
    )
    with client:
        response = client.get(f"/api/v1/public/{TOKEN}")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "public_profile.not_found"


def test_public_profile_route_returns_not_ready_for_incomplete_profiles() -> None:
    client, _, _ = make_client(
        link=link_document(token_hash=hash_public_token(TOKEN)), profile=incomplete_profile()
    )
    with client:
        response = client.get(f"/api/v1/public/{TOKEN}")

    assert response.status_code == 200
    assert response.json()["profile"]["display_name"] == "Alex Example"


def test_public_profile_route_returns_safe_503_on_dependency_failure() -> None:
    link_repository = FakePublicLinkRepository(link_document(token_hash=hash_public_token(TOKEN)))
    profile_repository = InMemoryProfileRepository(ready_profile())
    profile_repository.failure = RepositoryError("db secret")
    app = create_app(
        settings=settings(),
        database=FakeDatabase(ready=True),
        public_access_link_repository=link_repository,
        profile_repository=profile_repository,
    )

    with TestClient(app) as client:
        response = client.get(f"/api/v1/public/{TOKEN}")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "public_profile.service_unavailable"
    assert "db secret" not in response.text


def test_profile_preview_link_route_returns_public_url() -> None:
    link_repository = InMemoryLinkRepository(None)
    profile_repository = InMemoryProfileRepository(ready_profile())
    app = create_app(
        settings=settings(),
        database=FakeDatabase(ready=True),
        public_access_link_repository=link_repository,
        profile_repository=profile_repository,
    )
    app.dependency_overrides[get_current_user] = lambda: CurrentUserOutput(
        id=str(OWNER_ID),
        email="alex@example.test",
        role="user",
        created_at=NOW,
        updated_at=NOW,
    )

    with TestClient(app) as client:
        response = client.post("/api/v1/me/profile/public-preview-link")

    assert response.status_code == 200
    public_url = response.json()["public_url"]
    assert public_url.startswith("https://app.example/e/")
    assert link_repository.create_calls == [
        (
            str(PROFILE_ID),
            PublicLinkPurpose.STANDALONE,
            hash_public_token(public_url.rsplit("/", 1)[-1]),
        )
    ]


def test_profile_preview_link_routes_support_generate_regenerate_and_disable() -> None:
    link_repository = InMemoryLinkRepository(None)
    profile_repository = InMemoryProfileRepository(ready_profile())
    app = create_app(
        settings=settings(),
        database=FakeDatabase(ready=True),
        public_access_link_repository=link_repository,
        profile_repository=profile_repository,
    )
    app.dependency_overrides[get_current_user] = lambda: CurrentUserOutput(
        id=str(OWNER_ID),
        email="alex@example.test",
        role="user",
        created_at=NOW,
        updated_at=NOW,
    )

    with TestClient(app) as client:
        generated = client.post("/api/v1/me/profile/public-preview-link/generate")
        regenerated = client.post("/api/v1/me/profile/public-preview-link/regenerate")
        disabled = client.post("/api/v1/me/profile/public-preview-link/disable")

    assert generated.status_code == 200
    assert regenerated.status_code == 200
    assert disabled.status_code == 200
    assert generated.json()["action"] == "generate"
    assert regenerated.json()["action"] == "regenerate"
    assert regenerated.json()["status"] == "rotated"
    assert disabled.json()["action"] == "disable"
    assert disabled.json()["status"] == "disabled"
    assert disabled.json()["public_url"] is None
    assert "raw_token" not in generated.text + regenerated.text + disabled.text


@pytest.mark.asyncio
async def test_public_preview_link_route_issues_fresh_urls_each_time() -> None:
    link_repository = InMemoryLinkRepository(None)
    profile_repository = InMemoryProfileRepository(ready_profile())
    service = PublicProfileLinkService(
        link_repository, profile_repository, public_profile_base_url="https://app.example/e"
    )

    first = await service.create_preview_link(profile_id=PROFILE_ID)
    second = await service.create_preview_link(profile_id=PROFILE_ID)

    assert first.public_url is not None and second.public_url is not None
    assert first.public_url != second.public_url
    assert len(link_repository.links) == 2


@pytest.mark.asyncio
async def test_public_lookup_reflects_profile_updates_on_the_same_token() -> None:
    profile = ready_profile(blood_type="O+")
    profile_repository = InMemoryProfileRepository(profile)
    service = PublicProfileLookupService(
        InMemoryLinkRepository(link_document(token_hash=hash_public_token(TOKEN))),
        profile_repository,
    )

    first = await service.lookup(TOKEN)
    assert first.blood_type == "O+"

    profile_repository.profile = ready_profile(blood_type="A+")

    second = await service.lookup(TOKEN)
    assert second.blood_type == "A+"


@pytest.mark.asyncio
async def test_public_lookup_includes_safe_card_attribution_when_available() -> None:
    link = link_document(
        token_hash=hash_public_token(TOKEN),
        purpose=PublicLinkPurpose.CARD,
        label="Card access",
    )
    assignment = CardLinkAssignmentDocument.model_validate(
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
    result = await PublicProfileLookupService(
        InMemoryLinkRepository(link),
        InMemoryProfileRepository(ready_profile()),
        assignment_repository=InMemoryAssignmentRepository(assignment),
    ).lookup(TOKEN)

    assert result.display_name == "Alex Example"
    assert result.link_id == str(link.id)
    assert result.purpose is PublicLinkPurpose.CARD
    assert result.assignment_id == str(assignment.id)
    assert result.card_id == str(assignment.card_id)


@pytest.mark.asyncio
async def test_public_link_service_retries_duplicate_token_collisions() -> None:
    links = FlakyLifecycleLinkRepository()
    profiles = InMemoryProfileRepository(ready_profile())
    service = PublicProfileLinkService(
        links, profiles, public_profile_base_url="https://app.example/e"
    )

    created = await service.generate(profile_id=PROFILE_ID)
    rotated = await service.regenerate(profile_id=PROFILE_ID)

    assert created.status == "created"
    assert rotated.status == "rotated"
    assert created.public_url is not None and rotated.public_url is not None
    assert created.public_url != rotated.public_url


@pytest.mark.asyncio
async def test_public_profile_command_run_strips_raw_token(monkeypatch: pytest.MonkeyPatch) -> None:
    created = PublicProfileLinkResult(
        action="generate",
        status="created",
        profile_id=str(PROFILE_ID),
        public_url="https://app.example/e/token-1",
        raw_token="secret-token",
    )

    class FakeDatabaseHandle:
        def __init__(self) -> None:
            self.database = object()
            self.started = False
            self.closed = False

        async def start(self) -> None:
            self.started = True

        async def close(self) -> None:
            self.closed = True

    class FakeService:
        def __init__(self, *args: object, **kwargs: object) -> None:
            self.kwargs = kwargs

        async def generate(
            self, *, profile_id: str, now: object | None = None
        ) -> PublicProfileLinkResult:
            return created

        async def regenerate(
            self, *, profile_id: str, now: object | None = None
        ) -> PublicProfileLinkResult:
            return created

        async def disable(
            self, *, profile_id: str, now: object | None = None
        ) -> PublicProfileLinkResult:
            return PublicProfileLinkResult(
                action="disable",
                status="disabled",
                profile_id=profile_id,
                disabled=True,
            )

    fake_database = FakeDatabaseHandle()
    monkeypatch.setattr(public_profile_links_module, "get_settings", lambda: settings())
    monkeypatch.setattr(public_profile_links_module, "Database", lambda _settings: fake_database)
    monkeypatch.setattr(
        public_profile_links_module,
        "PublicAccessLinkRepository",
        lambda *args, **kwargs: object(),
    )
    monkeypatch.setattr(
        public_profile_links_module,
        "ProfileRepository",
        lambda *args, **kwargs: object(),
    )
    monkeypatch.setattr(public_profile_links_module, "PublicProfileLinkService", FakeService)

    result = await public_profile_links_module.run("generate", profile_id=str(PROFILE_ID))

    assert fake_database.started is True
    assert fake_database.closed is True
    assert result["action"] == "generate"
    assert result["public_url"] == "https://app.example/e/token-1"
    assert "raw_token" not in result


@pytest.mark.asyncio
async def test_public_profile_command_main_rejects_invalid_profile_id(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exit_error:
        await public_profile_links_module.main(["generate", "--profile-id", "not-an-object-id"])

    assert exit_error.value.code == 1
    output = capsys.readouterr().out
    assert "public_profile.not_found" in output
    assert "secret" not in output


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error_type, expected_code",
    [(PublicProfileNotFoundError, 404), (PublicProfileNotReadyError, 409)],
)
async def test_public_profile_command_main_prints_safe_service_errors(
    error_type: type[Exception],
    expected_code: int,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def fake_run(action: str, *, profile_id: str) -> dict[str, object]:
        raise error_type

    monkeypatch.setattr(public_profile_links_module, "run", fake_run)

    with pytest.raises(SystemExit) as exit_error:
        await public_profile_links_module.main(["disable", "--profile-id", str(PROFILE_ID)])

    assert exit_error.value.code == expected_code
    output = capsys.readouterr().out
    assert "public_profile" in output


@pytest.mark.asyncio
async def test_public_link_service_generates_rotates_and_disables_links() -> None:
    links = InMemoryLinkRepository()
    profiles = InMemoryProfileRepository(ready_profile())
    service = PublicProfileLinkService(
        links, profiles, public_profile_base_url="https://app.example/e"
    )

    created = await service.generate(profile_id=PROFILE_ID)
    cached = await service.generate(profile_id=PROFILE_ID)
    rotated = await service.regenerate(profile_id=PROFILE_ID)
    disabled = await service.disable(profile_id=PROFILE_ID)
    reactivated = await service.generate(profile_id=PROFILE_ID)

    assert created.status == "created"
    assert created.raw_token is not None
    assert created.public_url is not None and created.public_url.startswith(
        "https://app.example/e/"
    )
    assert created.raw_token not in links.link.model_dump_json()
    assert cached.status == "existing"
    assert cached.public_url == created.public_url
    assert rotated.status == "rotated"
    assert rotated.public_url is not None and rotated.public_url != created.public_url
    assert disabled.status == "disabled"
    assert disabled.disabled is True
    assert reactivated.status == "reactivated"
    assert reactivated.public_url is not None and reactivated.public_url != rotated.public_url
