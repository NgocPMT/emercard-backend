from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock

import pytest
from bson import ObjectId
from fastapi.testclient import TestClient
from pydantic import SecretStr

from emercard.core.config import Settings
from emercard.db import public_profile_links as public_profile_links_module
from emercard.db.repositories import RepositoryConflictError, RepositoryError
from emercard.main import create_app
from emercard.modules.cards import hash_public_token
from emercard.modules.profiles import ProfileDocument
from emercard.modules.public_links import (
    PublicAccessLinkDocument,
    PublicAccessLinkStatus,
    PublicProfileDisabledError,
    PublicProfileLinkResult,
    PublicProfileLinkService,
    PublicProfileLookupService,
    PublicProfileNotFoundError,
    PublicProfileNotReadyError,
    PublicProfileServiceUnavailableError,
)
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
    *, token_hash: str, status: PublicAccessLinkStatus = PublicAccessLinkStatus.ACTIVE
) -> PublicAccessLinkDocument:
    payload: dict[str, Any] = {
        "_id": ObjectId("507f1f77bcf86cd799439013"),
        "profile_id": PROFILE_ID,
        "token_hash": token_hash,
        "status": status,
        "created_at": NOW,
        "updated_at": NOW,
        "disabled_at": None if status is PublicAccessLinkStatus.ACTIVE else NOW,
    }
    return PublicAccessLinkDocument.model_validate(payload)


class InMemoryLinkRepository:
    def __init__(self, link: PublicAccessLinkDocument | None = None) -> None:
        self.link = link
        self.by_token_calls: list[str] = []
        self.by_profile_calls: list[str] = []
        self.create_calls: list[tuple[str, str]] = []
        self.rotate_calls: list[tuple[str, str]] = []
        self.disable_calls: list[str] = []

    async def find_by_token_hash(
        self, token_hash: str, *, session: object | None = None
    ) -> PublicAccessLinkDocument | None:
        self.by_token_calls.append(token_hash)
        if self.link is not None and self.link.token_hash == token_hash:
            return self.link
        return None

    async def find_by_profile_id(
        self, profile_id: ObjectId | str, *, session: object | None = None
    ) -> PublicAccessLinkDocument | None:
        self.by_profile_calls.append(str(profile_id))
        if self.link is not None and str(self.link.profile_id) == str(profile_id):
            return self.link
        return None

    async def create_for_profile(
        self,
        *,
        profile_id: ObjectId | str,
        token_hash: str,
        now: object | None = None,
        session: object | None = None,
    ) -> PublicAccessLinkDocument:
        self.create_calls.append((str(profile_id), token_hash))
        self.link = PublicAccessLinkDocument.model_validate(
            {
                "_id": ObjectId(),
                "profile_id": ObjectId(str(profile_id)),
                "token_hash": token_hash,
                "status": PublicAccessLinkStatus.ACTIVE,
                "created_at": now or NOW,
                "updated_at": now or NOW,
                "disabled_at": None,
            }
        )
        return self.link

    async def rotate_for_profile(
        self,
        *,
        profile_id: ObjectId | str,
        token_hash: str,
        now: object | None = None,
        session: object | None = None,
    ) -> PublicAccessLinkDocument:
        self.rotate_calls.append((str(profile_id), token_hash))
        self.link = PublicAccessLinkDocument.model_validate(
            {
                "_id": self.link.id if self.link is not None else ObjectId(),
                "profile_id": ObjectId(str(profile_id)),
                "token_hash": token_hash,
                "status": PublicAccessLinkStatus.ACTIVE,
                "created_at": self.link.created_at if self.link is not None else (now or NOW),
                "updated_at": now or NOW,
                "disabled_at": None,
            }
        )
        return self.link

    async def disable_for_profile(
        self,
        *,
        profile_id: ObjectId | str,
        now: object | None = None,
        session: object | None = None,
    ) -> PublicAccessLinkDocument | None:
        self.disable_calls.append(str(profile_id))
        if self.link is None or str(self.link.profile_id) != str(profile_id):
            return None
        self.link = PublicAccessLinkDocument.model_validate(
            {
                **self.link.model_dump(mode="python", by_alias=True),
                "status": PublicAccessLinkStatus.DISABLED,
                "updated_at": now or NOW,
                "disabled_at": now or NOW,
            }
        )
        return self.link


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


class FlakyLifecycleLinkRepository(InMemoryLinkRepository):
    def __init__(self, link: PublicAccessLinkDocument | None = None) -> None:
        super().__init__(link)
        self.create_failures = 1
        self.rotate_failures = 1

    async def create_for_profile(
        self,
        *,
        profile_id: ObjectId | str,
        token_hash: str,
        now: object | None = None,
        session: object | None = None,
    ) -> PublicAccessLinkDocument:
        if self.create_failures > 0:
            self.create_failures -= 1
            raise RepositoryConflictError("collision")
        return await super().create_for_profile(
            profile_id=profile_id, token_hash=token_hash, now=now, session=session
        )

    async def rotate_for_profile(
        self,
        *,
        profile_id: ObjectId | str,
        token_hash: str,
        now: object | None = None,
        session: object | None = None,
    ) -> PublicAccessLinkDocument:
        if self.rotate_failures > 0:
            self.rotate_failures -= 1
            raise RepositoryConflictError("collision")
        return await super().rotate_for_profile(
            profile_id=profile_id, token_hash=token_hash, now=now, session=session
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

    with pytest.raises(PublicProfileDisabledError):
        await service.lookup(TOKEN)


@pytest.mark.asyncio
async def test_public_lookup_rejects_not_ready_profiles() -> None:
    service = PublicProfileLookupService(
        InMemoryLinkRepository(link_document(token_hash=hash_public_token(TOKEN))),
        InMemoryProfileRepository(incomplete_profile()),
    )

    with pytest.raises(PublicProfileNotReadyError):
        await service.lookup(TOKEN)


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

    assert response.status_code == 410
    assert response.json()["error"]["code"] == "public_profile.disabled"


def test_public_profile_route_returns_not_ready_for_incomplete_profiles() -> None:
    client, _, _ = make_client(
        link=link_document(token_hash=hash_public_token(TOKEN)), profile=incomplete_profile()
    )
    with client:
        response = client.get(f"/api/v1/public/{TOKEN}")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "public_profile.not_ready"


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
