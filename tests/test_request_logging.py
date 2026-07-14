import logging

from emercard.api.middleware import (
    UvicornAccessLogRedactionFilter,
    install_uvicorn_access_log_redaction,
)
from emercard.main import create_app
from tests.conftest import FakeDatabase


def test_uvicorn_access_log_redaction_filter_redacts_public_and_emergency_tokens() -> None:
    filter_ = UvicornAccessLogRedactionFilter("/api/v1")
    record = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='%s - "%s %s HTTP/%s" %d',
        args=("127.0.0.1:12345", "GET", "/api/v1/public/abc123", "1.1", 200),
        exc_info=None,
    )

    assert filter_.filter(record) is True
    assert record.args == (
        "127.0.0.1:12345",
        "GET",
        "/api/v1/public/{token}",
        "1.1",
        200,
    )

    emergency_record = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='%s - "%s %s HTTP/%s" %d',
        args=("127.0.0.1:12345", "GET", "/api/v1/emergency/xyz987", "1.1", 404),
        exc_info=None,
    )

    assert filter_.filter(emergency_record) is True
    assert emergency_record.args == (
        "127.0.0.1:12345",
        "GET",
        "/api/v1/emergency/{token}",
        "1.1",
        404,
    )


def test_create_app_installs_uvicorn_access_log_redaction_filter(settings) -> None:
    install_uvicorn_access_log_redaction(settings.api_prefix)
    app = create_app(settings=settings, database=FakeDatabase(ready=True))
    assert app is not None

    access_logger = logging.getLogger("uvicorn.access")
    assert any(
        isinstance(filter_, UvicornAccessLogRedactionFilter) for filter_ in access_logger.filters
    )
