from fastapi.testclient import TestClient

from emercard.core.config import Settings
from tests.conftest import FakeDatabase


def test_health_does_not_require_database(client: tuple[TestClient, FakeDatabase]) -> None:
    test_client, database = client

    response = test_client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert database.ping_count == 0


def test_ready_succeeds_after_database_ping(client: tuple[TestClient, FakeDatabase]) -> None:
    test_client, database = client

    response = test_client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready", "database": "ok"}
    assert database.ping_count == 1


def test_ready_returns_safe_error_when_database_is_unavailable(
    settings: Settings,
) -> None:
    from emercard.main import create_app

    database = FakeDatabase(ready=False)
    app = create_app(settings=settings, database=database)  # type: ignore[arg-type]
    with TestClient(app) as test_client:
        response = test_client.get("/ready")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "database_unavailable"
    assert "mongodb" not in response.text.lower()
    assert database.ping_count == 1


def test_request_id_is_propagated_to_success_and_error_responses(
    client: tuple[TestClient, FakeDatabase],
) -> None:
    test_client, _ = client

    success = test_client.get("/health", headers={"X-Request-ID": "demo-request-1"})
    missing = test_client.get("/does-not-exist", headers={"X-Request-ID": "demo-request-2"})

    assert success.headers["X-Request-ID"] == "demo-request-1"
    assert missing.headers["X-Request-ID"] == "demo-request-2"
    assert missing.json()["error"]["request_id"] == "demo-request-2"
    assert "traceback" not in missing.text.lower()


def test_configured_origin_is_allowed_and_unknown_origin_is_not(
    client: tuple[TestClient, FakeDatabase],
) -> None:
    test_client, _ = client

    allowed = test_client.options(
        "/health",
        headers={
            "Origin": "http://localhost:4321",
            "Access-Control-Request-Method": "GET",
        },
    )
    blocked = test_client.options(
        "/health",
        headers={
            "Origin": "https://unapproved.example.com",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:4321"
    assert allowed.headers["access-control-allow-credentials"] == "true"
    assert "access-control-allow-origin" not in blocked.headers


def test_meta_contains_only_non_sensitive_settings(
    client: tuple[TestClient, FakeDatabase],
) -> None:
    test_client, _ = client

    response = test_client.get("/api/v1/meta")

    assert response.status_code == 200
    assert response.json()["environment"] == "test"
    assert "mongodb" not in response.text.lower()
