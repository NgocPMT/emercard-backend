from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from bson import ObjectId
from fastapi.testclient import TestClient
from pydantic import SecretStr

from emercard.api.admin_card_routes import (
    get_card_repository,
    get_card_service,
)
from emercard.core.config import Settings
from emercard.main import create_app
from emercard.modules.auth.security import hash_password
from emercard.modules.cards import (
    CardDocument,
    CardEncodingMismatchError,
    CardService,
    CardStatus,
    generate_serial,
    hash_public_token,
)
from emercard.modules.users.models import UserDocument
from tests.conftest import FakeDatabase
from tests.test_auth import InMemoryProfileRepository, InMemoryUserRepository

NOW = datetime(2026, 1, 1, tzinfo=UTC)
ADMIN_ID = ObjectId("507f1f77bcf86cd799439013")


def settings() -> Settings:
    return Settings(
        environment="test",
        auth_secret=SecretStr("test-auth-secret-012345678901234567890"),
        cors_origins=["http://localhost:4321"],
        public_card_base_url="https://app.example/e",
    )


def admin_repository() -> InMemoryUserRepository:
    repository = InMemoryUserRepository()
    repository.users[str(ADMIN_ID)] = UserDocument(
        _id=ADMIN_ID,
        email="admin@example.com",
        password_hash=hash_password("password-123"),
        role="admin",
        created_at=NOW,
        updated_at=NOW,
    )
    return repository


def blank_card() -> CardDocument:
    return CardDocument(
        _id=ObjectId(),
        serial=generate_serial(),
        token_hash=None,
        status=CardStatus.UNASSIGNED,
        is_current=False,
        created_at=NOW,
        updated_at=NOW,
    )


def managed_card(token: str) -> CardDocument:
    return CardDocument(
        _id=ObjectId(),
        serial=generate_serial(),
        token_hash=hash_public_token(token),
        status=CardStatus.UNASSIGNED,
        is_current=False,
        provisioned_at=NOW,
        created_at=NOW,
        updated_at=NOW,
    )


@pytest.mark.parametrize(
    ("method", "path", "payload", "headers"),
    [
        ("GET", "/api/v1/admin/cards", None, {}),
        ("GET", "/api/v1/admin/users/lookup?email=user@example.com", None, {}),
        ("POST", "/api/v1/admin/cards", None, {"Idempotency-Key": "route-test"}),
        ("POST", "/api/v1/admin/cards/card/confirm-encoding", {"public_url": "x"}, {}),
        ("POST", "/api/v1/admin/cards/card/issue", None, {}),
        ("POST", "/api/v1/admin/cards/card/void", None, {}),
        ("POST", "/api/v1/admin/cards/card/lost", None, {}),
        ("POST", "/api/v1/admin/cards/card/replace", None, {}),
    ],
)
def test_every_admin_card_route_requires_an_admin(
    method: str,
    path: str,
    payload: dict[str, str] | None,
    headers: dict[str, str],
) -> None:
    repository = admin_repository()
    app = create_app(
        settings=settings(),
        database=FakeDatabase(ready=True),
        auth_repository=repository,
        profile_repository=InMemoryProfileRepository(),
    )

    with TestClient(app) as client:
        missing = client.request(method, path, json=payload, headers=headers)
        client.post(
            "/api/v1/auth/register",
            json={"email": "user@example.com", "password": "password-123"},
        )
        client.post(
            "/api/v1/auth/login",
            json={"email": "user@example.com", "password": "password-123"},
        )
        normal_user = client.request(method, path, json=payload, headers=headers)

    assert missing.status_code == 401
    assert missing.json()["error"]["code"] == "auth.authentication_required"
    assert normal_user.status_code == 403
    assert normal_user.json()["error"]["code"] == "auth.forbidden"


def test_blank_card_response_is_safe_and_requires_idempotency_key() -> None:
    repository = admin_repository()
    card = blank_card()
    service = AsyncMock(spec=CardService)
    service.create_blank_card = AsyncMock(return_value=card)
    app = create_app(
        settings=settings(),
        database=FakeDatabase(ready=True),
        auth_repository=repository,
        profile_repository=InMemoryProfileRepository(),
    )
    app.dependency_overrides[get_card_service] = lambda: service

    with TestClient(app) as client:
        login = client.post(
            "/api/v1/auth/login",
            json={"email": "admin@example.com", "password": "password-123"},
        )
        missing_key = client.post("/api/v1/admin/cards")
        created = client.post(
            "/api/v1/admin/cards",
            headers={"Idempotency-Key": "create-1"},
        )

    assert login.status_code == 200
    assert missing_key.status_code == 422
    assert created.status_code == 201
    assert created.json()["encoding_state"] == "not_provisioned"
    assert "token_hash" not in created.text
    assert "public_token" not in created.text
    service.create_blank_card.assert_awaited_once_with(operation_key="create-1")


def test_admin_mutation_routes_return_safe_card_metadata() -> None:
    repository = admin_repository()
    card = managed_card("route-token")
    service = AsyncMock(spec=CardService)
    service.describe_admin_card = AsyncMock(return_value=(card, None, None))
    for method in ("confirm_encoding", "issue", "void"):
        setattr(service, method, AsyncMock(return_value=card))
    app = create_app(
        settings=settings(),
        database=FakeDatabase(ready=True),
        auth_repository=repository,
        profile_repository=InMemoryProfileRepository(),
    )
    app.dependency_overrides[get_card_service] = lambda: service

    with TestClient(app) as client:
        client.post(
            "/api/v1/auth/login",
            json={"email": "admin@example.com", "password": "password-123"},
        )
        responses = [
            client.post(
                f"/api/v1/admin/cards/{card.id}/confirm-encoding",
                json={"public_url": "https://app.example/e/route-token"},
            ),
            client.post(f"/api/v1/admin/cards/{card.id}/issue"),
            client.post(f"/api/v1/admin/cards/{card.id}/void"),
        ]

    assert [response.status_code for response in responses] == [200] * 3
    for response in responses:
        assert "token_hash" not in response.text
        assert "medical" not in response.text.lower()


def test_admin_inventory_list_and_detail_are_safe_and_cursor_paginated() -> None:
    repository = admin_repository()
    first = blank_card()
    second = blank_card()
    card_repository = AsyncMock()
    card_repository.list_admin = AsyncMock(return_value=[first, second])
    card_repository.find_by_id = AsyncMock(return_value=first)
    app = create_app(
        settings=settings(),
        database=FakeDatabase(ready=True),
        auth_repository=repository,
        profile_repository=InMemoryProfileRepository(),
    )
    service = AsyncMock(spec=CardService)
    service.describe_admin_card = AsyncMock(return_value=(first, None, None))
    app.dependency_overrides[get_card_repository] = lambda: card_repository
    app.dependency_overrides[get_card_service] = lambda: service

    with TestClient(app) as client:
        client.post(
            "/api/v1/auth/login",
            json={"email": "admin@example.com", "password": "password-123"},
        )
        listed = client.get("/api/v1/admin/cards?limit=1&encoding_state=not_provisioned")
        detail = client.get(f"/api/v1/admin/cards/{first.id}")

    assert listed.status_code == 200
    assert len(listed.json()["items"]) == 1
    assert listed.json()["next_cursor"]
    assert detail.status_code == 200
    assert detail.json()["encoding_state"] == "not_provisioned"
    assert "token_hash" not in listed.text + detail.text
    assert "medical" not in listed.text.lower() + detail.text.lower()
    card_repository.list_admin.assert_awaited_once()


def test_admin_card_errors_are_stable_and_do_not_leak_sensitive_input() -> None:
    repository = admin_repository()
    service = AsyncMock(spec=CardService)
    service.confirm_encoding = AsyncMock(
        side_effect=CardEncodingMismatchError("sensitive URL omitted")
    )
    card_repository = AsyncMock()
    card_repository.find_by_id = AsyncMock(return_value=None)
    app = create_app(
        settings=settings(),
        database=FakeDatabase(ready=True),
        auth_repository=repository,
        profile_repository=InMemoryProfileRepository(),
    )
    app.dependency_overrides[get_card_service] = lambda: service
    app.dependency_overrides[get_card_repository] = lambda: card_repository

    with TestClient(app) as client:
        client.post(
            "/api/v1/auth/login",
            json={"email": "admin@example.com", "password": "password-123"},
        )
        mismatch = client.post(
            "/api/v1/admin/cards/card/confirm-encoding",
            json={"public_url": "https://secret.example/token-value"},
        )
        unknown_user = client.get("/api/v1/admin/users/lookup?email=missing@example.com")
        unknown_card = client.get("/api/v1/admin/cards/not-an-object-id")

    assert mismatch.status_code == 422
    assert mismatch.json()["error"]["code"] == "card.encoding_mismatch"
    assert "secret" not in mismatch.text.lower()
    assert unknown_user.status_code == 404
    assert unknown_user.json()["error"]["code"] == "user.not_found"
    assert unknown_card.status_code == 404
    assert unknown_card.json()["error"]["code"] == "card.not_found"


def test_admin_user_lookup_returns_only_safe_account_fields() -> None:
    repository = admin_repository()
    user_id = ObjectId("507f1f77bcf86cd799439014")
    repository.users[str(user_id)] = UserDocument(
        _id=user_id,
        email="person@example.com",
        password_hash=hash_password("password-123"),
        role="user",
        created_at=NOW,
        updated_at=NOW,
    )
    app = create_app(
        settings=settings(),
        database=FakeDatabase(ready=True),
        auth_repository=repository,
        profile_repository=InMemoryProfileRepository(),
    )

    with TestClient(app) as client:
        client.post(
            "/api/v1/auth/login",
            json={"email": "admin@example.com", "password": "password-123"},
        )
        response = client.get("/api/v1/admin/users/lookup?email= PERSON@EXAMPLE.COM ")

    assert response.status_code == 200
    assert set(response.json()) == {"id", "email", "role", "created_at", "updated_at"}
    assert response.json()["id"] == str(user_id)
    assert response.json()["email"] == "person@example.com"
    assert "password" not in response.text.lower()
    assert "medical" not in response.text.lower()
