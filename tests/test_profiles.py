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
        self.upsert_calls: list[str] = []

    async def find_by_user_id(self, user_id: str) -> ProfileDocument | None:
        self.find_calls.append(user_id)
        if self.failure is not None:
            raise self.failure
        if self.profile is not None and str(self.profile.user_id) == user_id:
            return self.profile
        return None

    async def upsert_for_user(
        self,
        *,
        user_id: str,
        profile: ProfileUpsertInput,
    ) -> ProfileDocument:
        self.upsert_calls.append(user_id)
        if self.failure is not None:
            raise self.failure
        now = datetime.now(UTC)
        current_profile = self.profile is not None and str(self.profile.user_id) == user_id
        values: dict[str, Any] = {
            "_id": self.profile.id if current_profile else ObjectId(),
            "user_id": ObjectId(user_id),
            **profile.model_dump(mode="python"),
            "created_at": self.profile.created_at if current_profile else now,
            "updated_at": now,
            "public_access": (
                self.profile.public_access.model_dump(mode="python")
                if current_profile
                else {"token": None, "enabled": False, "published_at": None, "regenerated_at": None}
            ),
        }
        self.profile = ProfileDocument.model_validate(values)
        return self.profile


def settings() -> Settings:
    return Settings(
        environment="test",
        auth_secret=SecretStr("test-auth-secret-012345678901234567890"),
        cors_origins=["http://localhost:4321"],
    )


def profile_for(user: UserDocument, *, ready: bool = False) -> ProfileDocument:
    timestamp = datetime(2026, 1, 1, tzinfo=UTC)
    payload: dict[str, Any] = {
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
    if ready:
        payload.update(
            {
                "display_name": "Alex Example",
                "birth_year": 1995,
                "gender": "prefer_not_to_say",
                "blood_type": "O+",
                "emergency_contacts": [
                    {
                        "name": "Sam Example",
                        "relationship": "Friend",
                        "phone": "0901234567",
                    }
                ],
            }
        )
    return ProfileDocument.model_validate(payload)


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


def test_get_profile_returns_not_started_view_when_profile_is_missing() -> None:
    client, repository, user = make_client(profile=None)
    with client:
        login(client)
        response = client.get("/api/v1/me/profile")

    assert response.status_code == 200
    body = response.json()
    assert body["profile"] is None
    assert body["readiness"] == {
        "status": "not_started",
        "missing_fields": [
            "display_name",
            "birth_year",
            "gender",
            "blood_type",
            "emergency_contacts",
        ],
        "required_contact_count": 1,
        "completed_required_field_count": 0,
        "total_required_field_count": 5,
    }
    assert repository.find_calls == [str(user.id)]


def test_put_profile_creates_profile_and_returns_ready_view() -> None:
    client, repository, user = make_client(profile=None)
    with client:
        login(client)
        response = client.put(
            "/api/v1/me/profile",
            json={
                "display_name": "  Alex Example  ",
                "birth_year": 1995,
                "gender": "other",
                "blood_type": "O+",
                "critical_allergies": [" Penicillin ", "penicillin"],
                "important_conditions": ["Asthma"],
                "critical_medications": [],
                "emergency_note": "  Demo profile  ",
                "emergency_contacts": [
                    {"name": "Sam Example", "relationship": "Friend", "phone": "0901234567"}
                ],
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["profile"]["display_name"] == "Alex Example"
    assert body["profile"]["critical_allergies"] == ["Penicillin"]
    assert body["profile"]["emergency_note"] == "Demo profile"
    assert body["readiness"] == {
        "status": "ready",
        "missing_fields": [],
        "required_contact_count": 1,
        "completed_required_field_count": 5,
        "total_required_field_count": 5,
    }
    assert "id" not in body["profile"]
    assert repository.upsert_calls == [str(user.id)]


def test_put_profile_preserves_incomplete_drafts_and_created_at() -> None:
    client, repository, user = make_client(profile=None)
    with client:
        login(client)
        first = client.put(
            "/api/v1/me/profile",
            json={
                "display_name": "Alex Example",
                "critical_allergies": [],
                "important_conditions": [],
                "critical_medications": [],
                "emergency_contacts": [],
            },
        )
        first_created_at = first.json()["profile"]["created_at"]
        second = client.put(
            "/api/v1/me/profile",
            json={
                "display_name": "Alex Example",
                "birth_year": 1995,
                "gender": "male",
                "blood_type": "O+",
                "critical_allergies": [],
                "important_conditions": [],
                "critical_medications": [],
                "emergency_contacts": [
                    {"name": "Sam Example", "relationship": "Friend", "phone": "0901234567"}
                ],
            },
        )

    assert first.status_code == 200
    assert first.json()["readiness"]["status"] == "incomplete"
    assert second.status_code == 200
    assert second.json()["readiness"]["status"] == "ready"
    assert second.json()["profile"]["created_at"] == first_created_at
    assert second.json()["profile"]["updated_at"] != first_created_at
    assert repository.upsert_calls == [str(user.id), str(user.id)]


def test_invalid_profile_input_uses_shared_validation_error() -> None:
    client, repository, user = make_client(profile=None)
    with client:
        login(client)
        response = client.put(
            "/api/v1/me/profile",
            json={
                "display_name": "Alex",
                "birth_year": 1800,
                "gender": "not-a-gender",
                "critical_allergies": ["  ", "Penicillin"],
                "important_conditions": [],
                "critical_medications": [],
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
    assert repository.upsert_calls == []


@pytest.mark.parametrize(
    "patch",
    [
        {"birth_year": 1800},
        {
            "emergency_contacts": [{"name": "Sam", "relationship": "Family", "phone": "0900000000"}]
            * 6,
        },
        {"display_name": "   "},
    ],
)
def test_profile_limits_and_blank_values_use_shared_validation_error(
    patch: dict[str, Any],
) -> None:
    client, repository, user = make_client(profile=None)
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
    assert repository.upsert_calls == []


def test_profile_persistence_failure_returns_safe_503() -> None:
    client, repository, _ = make_client(profile=None)
    repository.failure = RepositoryError("database details")
    with client:
        login(client)
        response = client.put(
            "/api/v1/me/profile",
            json={
                "display_name": "Alex",
                "critical_allergies": [],
                "important_conditions": [],
                "critical_medications": [],
                "emergency_contacts": [],
            },
        )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "profile.service_unavailable"
    assert "database details" not in response.text
