"""Safe, consistent HTTP error responses."""

from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "unknown")


def error_payload(
    request: Request,
    *,
    code: str,
    message: str,
    details: Any = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "error": {
            "code": code,
            "message": message,
            "request_id": _request_id(request),
        }
    }
    if details is not None:
        payload["error"]["details"] = details
    return payload


def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    detail = exc.detail
    return JSONResponse(
        status_code=exc.status_code,
        content=error_payload(request, code="http_error", message=detail),
        headers=exc.headers,
    )


def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    details = [
        {
            "location": list(error.get("loc", ())),
            "message": error.get("msg", "Invalid value"),
            "type": error.get("type", ""),
        }
        for error in exc.errors()
    ]
    return JSONResponse(
        status_code=422,
        content=error_payload(
            request,
            code="validation_error",
            message="Request validation failed",
            details=details,
        ),
    )


def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    del exc
    details = {"exception": "internal_server_error"} if request.app.state.settings.debug else None
    return JSONResponse(
        status_code=500,
        content=error_payload(
            request,
            code="internal_server_error",
            message="An unexpected server error occurred",
            details=details,
        ),
    )
