from datetime import UTC, datetime
from typing import Any

import pytest
from bson import ObjectId
from fastapi.testclient import TestClient
from pydantic import SecretStr

from emercard.core.config import Settings
from emercard.db.repositories import RepositoryError
from emercard.main import create_app
from emercard.modules.auth.security import hash_password
from emercard.modules.profiles import ProfileDocument, ProfileUpsertInput
from emercard.modules.users import UserDocument
from tests.conftest import FakeDatabase


class InMemoryUserRepository:
    def __init__(self, user: UserDocument) -> None:
        self.user = user

    async def find_by_email(self, email: str) -> UserDocument | None:
        return self.user if self.user.email == email else None

    async def find_by_id(self, user_id: str) -> UserDocument | None:
        return self.user if str(self.user.id) == user_id else None


class InMemoryProfileRepository:
    def __init__(self, profile: ProfileDocument | None) -> None:
        self.profile = profile
        self.failure: Exception | None = None
        self.find_calls: list[str] = []
        self.replace_calls: list[str] = []

    async def find_by_user_id(self, user_id: str) -> ProfileDocument | None:
        self.find_calls.append(user_id)
        if self.failure is not None:
            raise self.failure
        if self.profile is not None and str(self.profile.user_id) == user_id:
            return self.profile
        return None

    async def replace_for_user(
        self,
        *,
        user_id: str,
        profile: ProfileUpsertInput,
    ) -> ProfileDocument | None:
        self.replace_calls.append(user_id)
        if self.failure is not None:
            raise self.failure
        if self.profile is None or str(self.profile.user_id) != user_id:
            return None
        now = datetime.now(UTC)
        values: dict[str, Any] = {
            "_id": self.profile.id,
            "user_id": self.profile.user_id,
            **profile.model_dump(mode="python"),
            "public_access": self.profile.public_access.model_dump(mode="python"),
            "created_at": self.profile.created_at,
            "updated_at": now,
        }
        self.profile = ProfileDocument.model_validate(values)
        return self.profile


def settings() -> Settings:
    return Settings(
        environment="test",
        auth_secret=SecretStr("test-auth-secret-012345678901234567890"),
        cors_origins=["http://localhost:4321"],
    )


def profile_for(user: UserDocument) -> ProfileDocument:
    timestamp = datetime(2026, 1, 1, tzinfo=UTC)
    return ProfileDocument.model_validate(
        {
            "_id": ObjectId(),
            "user_id": user.id,
            "critical_allergies": [],
            "important_conditions": [],
            "critical_medications": [],
            "emergency_contacts": [],
            "created_at": timestamp,
            "updated_at": timestamp,
            "public_access": {
                "token": "legacy-token",
                "enabled": True,
                "published_at": timestamp,
            },
        }
    )


def make_client(
    *, profile: ProfileDocument | None, repository: InMemoryProfileRepository | None = None
) -> tuple[TestClient, InMemoryProfileRepository, UserDocument]:
    user = UserDocument(
        _id=ObjectId(),
        email="person@example.com",
        password_hash=hash_password("password-123"),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    profile_repository = repository or InMemoryProfileRepository(profile)
    app = create_app(
        settings=settings(),
        database=FakeDatabase(ready=True),
        auth_repository=InMemoryUserRepository(user),
        profile_repository=profile_repository,
    )
    return TestClient(app), profile_repository, user


def login(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "person@example.com", "password": "password-123"},
    )
    assert response.status_code == 200


def test_profile_routes_require_authentication() -> None:
    client, _, _ = make_client(profile=None)
    with client:
        responses = [
            client.get("/api/v1/me/profile"),
            client.put("/api/v1/me/profile", json={}),
            client.get("/api/v1/me/profile/public-preview"),
        ]

    for response in responses:
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "auth.authentication_required"


def test_get_profile_returns_sanitized_authenticated_output() -> None:
    client, repository, user = make_client(profile=None)
    repository.profile = profile_for(user)
    with client:
        login(client)
        response = client.get("/api/v1/me/profile")

    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "incomplete"
    assert body["emergency_contacts"] == []
    assert body["important_conditions"] == []
    assert "public_access" not in body
    assert "id" not in body
    assert repository.find_calls == [str(user.id)]


def test_put_profile_saves_draft_and_returns_public_preview_without_internal_fields() -> None:
    client, repository, user = make_client(profile=None)
    repository.profile = profile_for(user)
    with client:
        login(client)
        saved = client.put(
            "/api/v1/me/profile",
            json={
                "display_name": "  Alex Example  ",
                "critical_allergies": [],
                "important_conditions": [],
                "critical_medications": [],
                "emergency_contacts": [],
            },
        )
        preview = client.get("/api/v1/me/profile/public-preview")

    assert saved.status_code == 200
    assert saved.json()["display_name"] == "Alex Example"
    assert saved.json()["state"] == "incomplete"
    assert "public_access" not in saved.json()
    assert "id" not in saved.json()
    assert saved.json()["emergency_contacts"] == []
    assert preview.status_code == 200
    assert preview.json()["display_name"] == "Alex Example"
    assert preview.json()["emergency_note"] is None
    assert "public_access" not in preview.json()
    assert "id" not in preview.json()
    assert repository.replace_calls == [str(user.id)]


def test_invalid_profile_input_uses_shared_validation_error() -> None:
    client, repository, user = make_client(profile=None)
    repository.profile = profile_for(user)
    with client:
        login(client)
        response = client.put(
            "/api/v1/me/profile",
            json={
                "display_name": "Alex",
                "gender": "not-a-gender",
                "emergency_contacts": [
                    {"name": "Sam", "relationship": "Family", "phone": "bad phone!"}
                ],
                "unexpected": "rejected",
            },
        )

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "validation_error"
    assert body["error"]["message"] == "Dữ liệu yêu cầu không hợp lệ."
    assert all(
        any(ord(character) > 127 for character in detail["message"])
        for detail in body["error"]["details"]
    )
    assert repository.replace_calls == []


@pytest.mark.parametrize(
    "patch",
    [
        {"birth_year": 1800},
        {"critical_allergies": ["allergy"] * 11},
        {
            "emergency_contacts": [{"name": "Sam", "relationship": "Family", "phone": "0900000000"}]
            * 6
        },
        {"display_name": "   "},
    ],
)
def test_profile_limits_and_blank_values_use_shared_validation_error(
    patch: dict[str, Any],
) -> None:
    client, repository, user = make_client(profile=None)
    repository.profile = profile_for(user)
    payload: dict[str, Any] = {
        "display_name": "Alex",
        "critical_allergies": [],
        "important_conditions": [],
        "critical_medications": [],
        "emergency_contacts": [],
    }
    payload.update(patch)
    with client:
        login(client)
        response = client.put("/api/v1/me/profile", json=payload)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert repository.replace_calls == []


def test_profile_reaches_ready_state_only_with_all_required_values() -> None:
    client, repository, user = make_client(profile=None)
    repository.profile = profile_for(user)
    payload = {
        "display_name": "Alex Example",
        "birth_year": 1995,
        "gender": "male",
        "blood_type": "O+",
        "critical_allergies": [],
        "important_conditions": [],
        "critical_medications": [],
        "emergency_contacts": [
            {"name": "Sam Example", "relationship": "Family", "phone": "0900000000"}
        ],
    }
    with client:
        login(client)
        ready = client.put("/api/v1/me/profile", json=payload)
        payload["blood_type"] = None
        incomplete = client.put("/api/v1/me/profile", json=payload)

    assert ready.status_code == 200
    assert ready.json()["state"] == "ready_to_publish"
    assert incomplete.status_code == 200
    assert incomplete.json()["state"] == "incomplete"
    assert incomplete.json()["emergency_contacts"][0]["phone"] == "0900000000"
    assert "id" not in incomplete.json()["emergency_contacts"][0]


def test_missing_profile_returns_safe_integrity_error_without_repair() -> None:
    client, repository, user = make_client(profile=None)
    with client:
        login(client)
        response = client.get(
            "/api/v1/me/profile",
            headers={"X-Request-ID": "profile-request-1"},
        )

    assert response.status_code == 500
    assert response.json()["error"] == {
        "code": "profile.provisioning_inconsistent",
        "message": "Không thể tải hồ sơ y tế.",
        "request_id": "profile-request-1",
    }
    assert repository.find_calls == [str(user.id)]
    assert repository.replace_calls == []


def test_profile_persistence_failure_returns_safe_503() -> None:
    client, repository, _ = make_client(profile=None)
    repository.failure = RepositoryError("database details")
    with client:
        login(client)
        response = client.get("/api/v1/me/profile")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "profile.service_unavailable"
    assert "database details" not in response.text
