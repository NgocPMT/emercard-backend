"""Safe, consistent HTTP error responses."""

from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from emercard.modules.auth.exceptions import AuthError
from emercard.modules.profiles.exceptions import ProfileError


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


def auth_exception_handler(request: Request, exc: AuthError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=error_payload(request, code=exc.code, message=exc.message),
    )


def profile_exception_handler(request: Request, exc: ProfileError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=error_payload(request, code=exc.code, message=exc.message),
    )


def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    messages = {
        404: "Không tìm thấy tài nguyên yêu cầu.",
        405: "Phương thức yêu cầu không được hỗ trợ.",
    }
    message = messages.get(exc.status_code, "Yêu cầu không hợp lệ.")
    return JSONResponse(
        status_code=exc.status_code,
        content=error_payload(request, code="http_error", message=message),
        headers=exc.headers,
    )


def _validation_message(error: dict[str, Any]) -> str:
    """Convert Pydantic's technical validation messages to Vietnamese."""

    error_type = str(error.get("type", ""))
    if error_type == "missing":
        return "Trường này là bắt buộc."
    if error_type == "extra_forbidden":
        return "Trường này không được phép."
    if error_type in {"string_type", "string_parsing"}:
        return "Giá trị phải là chuỗi ký tự."
    if error_type in {"int_type", "int_parsing"}:
        return "Giá trị phải là số nguyên."
    if error_type == "bool_type":
        return "Giá trị phải là đúng hoặc sai."
    if error_type in {"list_type", "tuple_type", "set_type"}:
        return "Giá trị phải là danh sách."
    if error_type in {"literal_error", "enum"}:
        return "Giá trị được chọn không hợp lệ."
    if error_type in {"none_required", "nullable_type"}:
        return "Giá trị phải để trống."
    if error_type in {"greater_than_equal", "greater_than"}:
        return "Giá trị chưa đạt mức tối thiểu cho phép."
    if error_type in {"less_than_equal", "less_than"}:
        return "Giá trị vượt quá mức cho phép."
    if error_type in {"string_too_short", "string_too_long"}:
        return "Độ dài văn bản không hợp lệ."
    if error_type == "value_error":
        message = str(error.get("msg", ""))
        if message.startswith("Value error, "):
            message = message.removeprefix("Value error, ")
        if any(character in message for character in "ăâđêôơưĂÂĐÊÔƠƯ"):  # already localized
            return message
        if "email" in message.lower():
            return "Email không hợp lệ."
        return "Giá trị không hợp lệ."
    return "Giá trị không hợp lệ."


def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    details = [
        {
            "location": list(error.get("loc", ())),
            "message": _validation_message(error),
            "type": error.get("type", ""),
        }
        for error in exc.errors()
    ]
    return JSONResponse(
        status_code=422,
        content=error_payload(
            request,
            code="validation_error",
            message="Dữ liệu yêu cầu không hợp lệ.",
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
            message="Đã xảy ra lỗi không mong muốn. Vui lòng thử lại sau.",
            details=details,
        ),
    )
