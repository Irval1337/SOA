from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from generated.models import ErrorCode, ErrorResponse, FieldViolation, ValidationErrorDetails, ViolationLocation
from generated.responses import ContractJSONResponse

HTTP_STATUS: dict[ErrorCode, int] = {
    ErrorCode.VALIDATION_ERROR: 400,
    ErrorCode.TOKEN_INVALID: 401,
    ErrorCode.TOKEN_EXPIRED: 401,
    ErrorCode.REFRESH_TOKEN_INVALID: 401,
    ErrorCode.INVALID_CREDENTIALS: 401,
    ErrorCode.ACCESS_DENIED: 403,
    ErrorCode.ORDER_OWNERSHIP_VIOLATION: 403,
    ErrorCode.PRODUCT_NOT_FOUND: 404,
    ErrorCode.ORDER_NOT_FOUND: 404,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.METHOD_NOT_ALLOWED: 405,
    ErrorCode.PRODUCT_INACTIVE: 409,
    ErrorCode.ORDER_HAS_ACTIVE: 409,
    ErrorCode.INVALID_STATE_TRANSITION: 409,
    ErrorCode.INSUFFICIENT_STOCK: 409,
    ErrorCode.USER_ALREADY_EXISTS: 409,
    ErrorCode.PROMO_CODE_ALREADY_EXISTS: 409,
    ErrorCode.PROMO_CODE_INVALID: 422,
    ErrorCode.PROMO_CODE_MIN_AMOUNT: 422,
    ErrorCode.ORDER_LIMIT_EXCEEDED: 429,
    ErrorCode.INTERNAL_ERROR: 500,
    ErrorCode.SERVICE_UNAVAILABLE: 503,
}

LOCATIONS = {location.value for location in ViolationLocation}


class ApiError(Exception):
    def __init__(
        self,
        code: ErrorCode,
        message: str,
        details: Mapping[str, Any] | BaseModel | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details.model_dump(mode="python") if isinstance(details, BaseModel) else details
        self.headers = dict(headers or {})

    @property
    def status_code(self) -> int:
        return HTTP_STATUS[self.code]


def validation_error(violations: Iterable[FieldViolation], message: str = "Request validation failed") -> ApiError:
    return ApiError(ErrorCode.VALIDATION_ERROR, message, ValidationErrorDetails(violations=list(violations)))


def error_response(
    code: ErrorCode,
    message: str,
    details: Mapping[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
) -> ContractJSONResponse:
    body = ErrorResponse(error_code=code, message=message, details=details)
    return ContractJSONResponse(
        body.model_dump(mode="python", exclude_none=True),
        status_code=HTTP_STATUS[code],
        headers=dict(headers) if headers else None,
    )


def _field_path(loc: Sequence[Any]) -> str:
    path = ""
    for part in loc:
        if isinstance(part, int):
            path += f"[{part}]"
        else:
            path += f".{part}" if path else str(part)
    return path


def to_violation(error: Mapping[str, Any]) -> FieldViolation:
    loc = tuple(error.get("loc", ()))
    location = loc[0] if loc and loc[0] in LOCATIONS else "body"
    rest = loc[1:] if loc and loc[0] in LOCATIONS else loc
    if error.get("type") == "json_invalid":
        rest = ()
    return FieldViolation(
        field=_field_path(rest) or location,
        location=ViolationLocation(location),
        code=str(error.get("type", "invalid")),
        message=str(error.get("msg", "Invalid value")),
    )


async def _handle_api_error(_: Request, exc: ApiError) -> ContractJSONResponse:
    return error_response(exc.code, exc.message, exc.details, exc.headers)


async def _handle_validation_error(_: Request, exc: RequestValidationError) -> ContractJSONResponse:
    error = validation_error(to_violation(e) for e in exc.errors())
    return error_response(error.code, error.message, error.details)


_HTTP_EXCEPTION_CODES = {
    400: (ErrorCode.VALIDATION_ERROR, "Bad request"),
    401: (ErrorCode.TOKEN_INVALID, "Not authenticated"),
    403: (ErrorCode.ACCESS_DENIED, "Access denied"),
    404: (ErrorCode.NOT_FOUND, "Resource not found"),
    405: (ErrorCode.METHOD_NOT_ALLOWED, "Method not allowed"),
}


async def _handle_http_exception(_: Request, exc: StarletteHTTPException) -> ContractJSONResponse:
    code, message = _HTTP_EXCEPTION_CODES.get(exc.status_code, (ErrorCode.INTERNAL_ERROR, "Internal server error"))
    return error_response(code, message, headers=exc.headers)


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ApiError, _handle_api_error)
    app.add_exception_handler(RequestValidationError, _handle_validation_error)
    app.add_exception_handler(StarletteHTTPException, _handle_http_exception)
