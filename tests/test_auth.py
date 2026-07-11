from datetime import UTC, datetime, timedelta

import jwt
import pytest
from bson import ObjectId
from fastapi.testclient import TestClient
from pydantic import SecretStr

from emercard.core.config import Settings
from emercard.db.repositories import RepositoryConflictError
from emercard.main import create_app
from emercard.modules.auth.security import (
    hash_password,
    issue_session_token,
    validate_session_token,
    verify_password,
)
from emercard.modules.users.models import UserDocument
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
        now: datetime | None = None,
    ) -> UserDocument:
        if self.force_conflict or any(user.email == email for user in self.users.values()):
            raise RepositoryConflictError("duplicate")
        timestamp = now or datetime.now(UTC)
        user = UserDocument(
            _id=ObjectId(),
            email=email,
            password_hash=password_hash,
            created_at=timestamp,
            updated_at=timestamp,
        )
        self.users[str(user.id)] = user
        return user


def make_settings() -> Settings:
    return Settings(
        environment="test",
        auth_secret=SecretStr("test-auth-secret-012345678901234567890"),
        cors_origins=["http://localhost:4321"],
    )


def make_client(repository: InMemoryUserRepository) -> TestClient:
    return TestClient(
        create_app(
            settings=make_settings(),
            database=FakeDatabase(ready=True),
            auth_repository=repository,
        )
    )


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
    with make_client(user_repository) as client:
        response = client.post(
            "/api/v1/auth/register",
            json={"email": " Person@Example.com ", "password": "password-123"},
        )

    assert response.status_code == 201
    assert set(response.json()) == {"id", "email", "created_at", "updated_at"}
    assert response.json()["email"] == "person@example.com"
    assert "set-cookie" not in response.headers
    stored = next(iter(user_repository.users.values()))
    assert stored.password_hash != "password-123"


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
