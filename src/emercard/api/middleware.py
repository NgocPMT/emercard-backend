"""Request correlation, emergency privacy headers, rate limiting, and safe access logging."""

from __future__ import annotations

import logging
import re
import time
from collections import deque
from collections.abc import Awaitable, Callable
from uuid import uuid4

from fastapi import Request, Response
from fastapi.responses import JSONResponse

from emercard.api.errors import error_payload

logger = logging.getLogger("emercard.request")
_REQUEST_ID_PATTERN = r"^[A-Za-z0-9._-]{1,128}$"
_LOOKUP_HEADERS = {
    "Cache-Control": "no-store",
    "Pragma": "no-cache",
    "X-Robots-Tag": "noindex, nofollow, noarchive",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
}


class UvicornAccessLogRedactionFilter(logging.Filter):
    """Redact bearer-token request paths from Uvicorn access logs."""

    def __init__(self, api_prefix: str) -> None:
        super().__init__()
        prefix = api_prefix.rstrip("/")
        self._route_templates = {
            f"{prefix}/public/": f"{prefix}/public/{{token}}",
            f"{prefix}/emergency/": f"{prefix}/emergency/{{token}}",
        }

    def filter(self, record: logging.LogRecord) -> bool:
        if record.name != "uvicorn.access":
            return True
        args = record.args
        if not isinstance(args, tuple) or len(args) < 5:
            return True
        path = args[2]
        if not isinstance(path, str):
            return True
        redacted_path = _redact_access_path(path, self._route_templates)
        if redacted_path == path:
            return True
        record.args = (args[0], args[1], redacted_path, args[3], args[4], *args[5:])
        return True


class EmergencyRateLimiter:
    """Small in-process sliding-window limiter for anonymous emergency requests."""

    def __init__(self, *, window_seconds: int, burst: int) -> None:
        self._window_seconds = window_seconds
        self._burst = burst
        self._requests: dict[str, deque[float]] = {}

    @property
    def retry_after_seconds(self) -> int:
        return self._window_seconds

    def allow(self, client_key: str, *, now: float | None = None) -> bool:
        current = time.monotonic() if now is None else now
        cutoff = current - self._window_seconds
        self._prune(cutoff)
        timestamps = self._requests.setdefault(client_key, deque())
        if len(timestamps) >= self._burst:
            return False
        timestamps.append(current)
        return True

    def _prune(self, cutoff: float) -> None:
        for key, timestamps in list(self._requests.items()):
            while timestamps and timestamps[0] <= cutoff:
                timestamps.popleft()
            if not timestamps:
                del self._requests[key]


def _is_emergency_path(request: Request) -> bool:
    prefix = request.app.state.settings.api_prefix.rstrip("/")
    base = f"{prefix}/emergency"
    return request.url.path == base or request.url.path.startswith(f"{base}/")


def _is_public_profile_path(request: Request) -> bool:
    prefix = request.app.state.settings.api_prefix.rstrip("/")
    base = f"{prefix}/public"
    return request.url.path == base or request.url.path.startswith(f"{base}/")


def _lookup_route_template(request: Request) -> str:
    prefix = request.app.state.settings.api_prefix.rstrip("/")
    if _is_public_profile_path(request):
        return f"{prefix}/public/{{token}}"
    return f"{prefix}/emergency/{{token}}"


def _client_key(request: Request) -> str:
    # Forwarded headers are intentionally ignored until a trusted-proxy policy exists.
    return request.client.host if request.client is not None else "unknown"


async def request_context_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    """Attach request IDs, protect emergency responses, and log no bearer material."""

    incoming_id = request.headers.get("X-Request-ID", "")
    request_id = incoming_id if re.fullmatch(_REQUEST_ID_PATTERN, incoming_id) else str(uuid4())
    request.state.request_id = request_id
    emergency = _is_emergency_path(request)
    public_profile = _is_public_profile_path(request)
    lookup = emergency or public_profile
    request_route = _lookup_route_template(request) if lookup else request.url.path
    started = time.perf_counter()
    rate_limited = False

    if emergency:
        limiter: EmergencyRateLimiter = request.app.state.emergency_rate_limiter
        if not limiter.allow(_client_key(request)):
            rate_limited = True
            response: Response = JSONResponse(
                status_code=429,
                content=error_payload(
                    request,
                    code="rate_limit.exceeded",
                    message="Quá nhiều yêu cầu. Vui lòng thử lại sau.",
                ),
                headers={"Retry-After": str(limiter.retry_after_seconds)},
            )
        else:
            response = await call_next(request)
    else:
        response = await call_next(request)

    if lookup:
        for name, value in _LOOKUP_HEADERS.items():
            response.headers[name] = value

    duration_ms = (time.perf_counter() - started) * 1000
    logger.info(
        "request completed",
        extra={
            "request_id": request_id,
            "method": request.method,
            "route": request_route,
            "status_code": response.status_code,
            "duration_ms": round(duration_ms, 2),
            "outcome": (
                "rate_limited"
                if rate_limited
                else _lookup_outcome(response.status_code)
                if lookup
                else "other"
            ),
        },
    )
    response.headers["X-Request-ID"] = request_id
    return response


def install_uvicorn_access_log_redaction(api_prefix: str) -> None:
    """Attach bearer-token path redaction to the Uvicorn access logger."""

    access_logger = logging.getLogger("uvicorn.access")
    if any(
        isinstance(filter_, UvicornAccessLogRedactionFilter) for filter_ in access_logger.filters
    ):
        return
    access_logger.addFilter(UvicornAccessLogRedactionFilter(api_prefix))


def _redact_access_path(path: str, route_templates: dict[str, str]) -> str:
    for prefix, template in route_templates.items():
        if not path.startswith(prefix):
            continue
        remainder = path[len(prefix) :].rstrip("/")
        if remainder.endswith("/location-alert"):
            token = remainder[: -len("/location-alert")].rstrip("/")
            if token and "/" not in token and "?" not in token and "#" not in token:
                return f"{template}/location-alert"
        if remainder and "/" not in remainder and "?" not in remainder and "#" not in remainder:
            return template
    return path


def _lookup_outcome(status_code: int) -> str:
    if status_code == 200:
        return "success"
    if status_code == 404:
        return "not_found"
    if status_code == 409:
        return "not_ready"
    if status_code == 410:
        return "disabled"
    if status_code == 429:
        return "rate_limited"
    if status_code >= 500:
        return "service_error"
    return "other"
