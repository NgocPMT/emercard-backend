from datetime import UTC, datetime, timedelta

import jwt
import pytest
from bson import ObjectId
from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient
from pydantic import SecretStr

from emercard.api.auth_routes import require_admin
from emercard.core.config import Settings
from emercard.db.repositories import RepositoryConflictError
from emercard.main import create_app
from emercard.modules.auth.security import (
    hash_password,
    issue_session_token,
    validate_session_token,
    verify_password,
)
from emercard.modules.profiles.models import ProfileDocument
from emercard.modules.users.models import CurrentUserOutput, UserDocument
from tests.conftest import FakeDatabase


class InMemoryUserRepository:
    def __init__(self) -> None:
        self.users: dict[str, UserDocument] = {}
        self.force_conflict = False

    async def find_by_email(self, email: str) -> UserDocument | None:
        return next((user for user in self.users.values() if user.email == email), None)

    async def find_by_id(self, user_id: str) -> UserDocument | None:
        return self.users.get(user_id)

    async def create(
        self,
        *,
        email: str,
        password_hash: str,
        role: str = "user",
        now: datetime | None = None,
    ) -> UserDocument:
        if self.force_conflict or any(user.email == email for user in self.users.values()):
            raise RepositoryConflictError("duplicate")
        timestamp = now or datetime.now(UTC)
        user = UserDocument(
            _id=ObjectId(),
            email=email,
            password_hash=password_hash,
            role=role,  # type: ignore[arg-type]
            created_at=timestamp,
            updated_at=timestamp,
        )
        self.users[str(user.id)] = user
        return user


class InMemoryProfileRepository:
    def __init__(self) -> None:
        self.profiles: dict[str, ProfileDocument] = {}
        self.force_failure = False

    async def ensure_for_user(self, *, user_id: str) -> ProfileDocument:
        if self.force_failure:
            raise RepositoryConflictError("profile provisioning failed")
        if user_id in self.profiles:
            return self.profiles[user_id]
        timestamp = datetime.now(UTC)
        profile = ProfileDocument.model_validate(
            {
                "_id": ObjectId(),
                "user_id": ObjectId(user_id),
                "created_at": timestamp,
                "updated_at": timestamp,
            }
        )
        self.profiles[user_id] = profile
        return profile


def make_settings() -> Settings:
    return Settings(
        environment="test",
        auth_secret=SecretStr("test-auth-secret-012345678901234567890"),
        cors_origins=["http://localhost:4321"],
    )


def make_client(
    repository: InMemoryUserRepository,
    profile_repository: InMemoryProfileRepository | None = None,
    *,
    with_admin_probe: bool = False,
) -> TestClient:
    app = create_app(
        settings=make_settings(),
        database=FakeDatabase(ready=True),
        auth_repository=repository,
        profile_repository=profile_repository or InMemoryProfileRepository(),
    )
    if with_admin_probe:
        admin_router = APIRouter(prefix="/admin")

        @admin_router.get("/probe")
        async def admin_probe(  # pyright: ignore[reportUnusedFunction]
            user: CurrentUserOutput = Depends(require_admin),  # noqa: B008
        ) -> dict[str, str]:
            return {"user_id": str(user.id)}

        app.include_router(admin_router)
    return TestClient(app)


def test_password_hashing_does_not_store_or_accept_plaintext_hashes() -> None:
    password = "correct horse battery staple"
    password_hash = hash_password(password)

    assert password_hash != password
    assert verify_password(password, password_hash)
    assert not verify_password("incorrect password", password_hash)
    assert not verify_password(password, "not-a-password-hash")


def test_session_tokens_require_expected_claims_and_signature() -> None:
    settings = make_settings()
    token = issue_session_token("507f1f77bcf86cd799439011", settings)
    claims = jwt.decode(
        token,
        settings.auth_secret.get_secret_value(),  # type: ignore[union-attr]
        algorithms=[settings.auth_algorithm],
    )

    assert set(claims) == {"sub", "iat", "exp"}
    assert validate_session_token(token, settings) == "507f1f77bcf86cd799439011"

    with pytest.raises(ValueError):
        validate_session_token(f"{token}tampered", settings)
    with pytest.raises(ValueError):
        validate_session_token("not-a-jwt", settings)


def test_session_tokens_reject_expiry_and_unsupported_algorithm() -> None:
    settings = make_settings()
    now = datetime.now(UTC)
    expired = jwt.encode(
        {"sub": "user", "iat": now - timedelta(seconds=60), "exp": now - timedelta(seconds=31)},
        settings.auth_secret.get_secret_value(),  # type: ignore[union-attr]
        algorithm="HS256",
    )
    wrong_algorithm = jwt.encode(
        {"sub": "user", "iat": now, "exp": now + timedelta(minutes=1)},
        settings.auth_secret.get_secret_value(),  # type: ignore[union-attr]
        algorithm="HS384",
    )

    with pytest.raises(ValueError):
        validate_session_token(expired, settings)
    with pytest.raises(ValueError):
        validate_session_token(wrong_algorithm, settings)


def test_register_returns_user_without_authenticating() -> None:
    user_repository = InMemoryUserRepository()
    profile_repository = InMemoryProfileRepository()
    with make_client(user_repository, profile_repository) as client:
        response = client.post(
            "/api/v1/auth/register",
            json={"email": " Person@Example.com ", "password": "password-123"},
        )

    assert response.status_code == 201
    assert set(response.json()) == {"id", "email", "role", "created_at", "updated_at"}
    assert response.json()["email"] == "person@example.com"
    assert response.json()["role"] == "user"
    assert "set-cookie" not in response.headers
    stored = next(iter(user_repository.users.values()))
    assert stored.password_hash != "password-123"
    assert stored.role == "user"
    assert len(profile_repository.profiles) == 1


def test_registration_does_not_return_success_when_profile_provisioning_fails() -> None:
    user_repository = InMemoryUserRepository()
    profile_repository = InMemoryProfileRepository()
    profile_repository.force_failure = True
    with make_client(user_repository, profile_repository) as client:
        response = client.post(
            "/api/v1/auth/register",
            json={"email": "person@example.com", "password": "password-123"},
        )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "auth.registration_provisioning_failed"
    assert len(user_repository.users) == 1
    assert len(profile_repository.profiles) == 0


def test_admin_routes_require_an_admin_role() -> None:
    repository = InMemoryUserRepository()
    with make_client(repository, with_admin_probe=True) as client:
        missing = client.get("/admin/probe")
        client.post(
            "/api/v1/auth/register",
            json={"email": "user@example.com", "password": "password-123"},
        )
        client.post(
            "/api/v1/auth/login",
            json={"email": "user@example.com", "password": "password-123"},
        )
        normal_user = client.get("/admin/probe")

    assert missing.status_code == 401
    assert missing.json()["error"]["code"] == "auth.authentication_required"
    assert normal_user.status_code == 403
    assert normal_user.json()["error"] == {
        "code": "auth.forbidden",
        "message": "You do not have permission to perform this action.",
        "request_id": normal_user.json()["error"]["request_id"],
    }


def test_admin_role_can_access_admin_routes() -> None:
    repository = InMemoryUserRepository()
    admin = UserDocument(
        _id=ObjectId(),
        email="admin@example.com",
        password_hash=hash_password("password-123"),
        role="admin",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    repository.users[str(admin.id)] = admin

    with make_client(repository, with_admin_probe=True) as client:
        login = client.post(
            "/api/v1/auth/login",
            json={"email": "admin@example.com", "password": "password-123"},
        )
        response = client.get("/admin/probe")

    assert login.status_code == 200
    assert response.status_code == 200
    assert response.json() == {"user_id": str(admin.id)}


def test_login_me_and_logout_use_the_configured_cookie() -> None:
    repository = InMemoryUserRepository()
    with make_client(repository) as client:
        registered = client.post(
            "/api/v1/auth/register",
            json={"email": "person@example.com", "password": "password-123"},
        )
        login = client.post(
            "/api/v1/auth/login",
            json={"email": "PERSON@example.com", "password": "password-123"},
        )
        current = client.get("/api/v1/me")
        logout = client.post("/api/v1/auth/logout")
        after_logout = client.get("/api/v1/me")

    assert registered.status_code == 201
    assert login.status_code == 200
    assert "emercard_session=" in login.headers["set-cookie"]
    cookie = login.headers["set-cookie"].lower()
    assert "max-age=900" in cookie
    assert "path=/" in cookie
    assert "samesite=lax" in cookie
    assert "httponly" in cookie
    assert "secure" not in cookie
    assert current.status_code == 200
    assert current.json()["email"] == "person@example.com"
    assert logout.status_code == 204
    assert logout.content == b""
    assert "max-age=0" in logout.headers["set-cookie"].lower()
    assert after_logout.status_code == 401
    assert after_logout.json()["error"]["code"] == "auth.authentication_required"


def test_deleted_user_subject_is_an_invalid_session() -> None:
    repository = InMemoryUserRepository()
    with make_client(repository) as client:
        client.post(
            "/api/v1/auth/register",
            json={"email": "person@example.com", "password": "password-123"},
        )
        login = client.post(
            "/api/v1/auth/login",
            json={"email": "person@example.com", "password": "password-123"},
        )
        repository.users.clear()
        current = client.get("/api/v1/me")

    assert login.status_code == 200
    assert current.status_code == 401
    assert current.json()["error"]["code"] == "auth.invalid_session"


def test_authentication_errors_are_safe_and_keep_request_ids() -> None:
    repository = InMemoryUserRepository()
    with make_client(repository) as client:
        duplicate_first = client.post(
            "/api/v1/auth/register",
            json={"email": "person@example.com", "password": "password-123"},
        )
        duplicate = client.post(
            "/api/v1/auth/register",
            headers={"X-Request-ID": "auth-request-1"},
            json={"email": "PERSON@example.com", "password": "password-123"},
        )
        unknown = client.post(
            "/api/v1/auth/login",
            json={"email": "unknown@example.com", "password": "password-123"},
        )
        wrong_password = client.post(
            "/api/v1/auth/login",
            json={"email": "person@example.com", "password": "wrong-pass"},
        )
        missing = client.get("/api/v1/me")
        client.cookies.set("emercard_session", "invalid-token")
        invalid = client.get("/api/v1/me")

    assert duplicate_first.status_code == 201
    assert duplicate.status_code == 409
    assert duplicate.json()["error"] == {
        "code": "auth.email_already_registered",
        "message": "An account with this email already exists.",
        "request_id": "auth-request-1",
    }
    assert unknown.status_code == wrong_password.status_code == 401
    assert unknown.json()["error"]["code"] == "auth.invalid_credentials"
    assert wrong_password.json()["error"]["code"] == "auth.invalid_credentials"
    assert missing.json()["error"]["code"] == "auth.authentication_required"
    assert invalid.json()["error"]["code"] == "auth.invalid_session"
    assert "password-123" not in duplicate.text + unknown.text + wrong_password.text
    assert "invalid-token" not in invalid.text
