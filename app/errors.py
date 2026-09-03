"""Error model and exception handlers (BUILD_SPEC §18).

Every error leaves the system in the §18.1 envelope. The codes in ERROR_STATUS
are the only codes this system emits — the same strings appear in the API
response, the audit payload, the guard console and the README. Never a
near-miss.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

# §18.2 — code to HTTP status.
ERROR_STATUS: dict[str, int] = {
    # merchant
    "MERCHANT_AUTH_FAILED": 401,
    "MERCHANT_NOT_FOUND": 404,
    "PRODUCT_NOT_FOUND": 404,
    "CART_NOT_FOUND": 404,
    "QUOTE_NOT_FOUND": 404,
    "ORDER_NOT_FOUND": 404,
    # §8.1's mandate endpoints are new in M5; §18.2 enumerates the *_NOT_FOUND
    # family without naming this one. Same family, same status.
    "MANDATE_NOT_FOUND": 404,
    # Same family again, for the §11.6 agent session endpoints.
    "SESSION_NOT_FOUND": 404,
    "CART_NOT_OPEN": 409,
    "IDEMPOTENCY_KEY_REQUIRED": 400,
    # checkout validator (§10)
    "MERCHANT_MANDATE_INVALID": 409,
    "MERCHANT_OUT_OF_STOCK": 409,
    "MERCHANT_PRICE_DRIFT": 409,
    "MERCHANT_QUOTE_STALE": 409,
    # transaction guard (§9.2) — the nine block codes
    "MANDATE_SIGNATURE_INVALID": 403,
    "MANDATE_NOT_ACTIVE": 403,
    "MANDATE_EXPIRED": 403,
    "MERCHANT_NOT_ALLOWED": 403,
    "AMOUNT_EXCEEDS_MANDATE": 403,
    "CUMULATIVE_CAP_EXCEEDED": 403,
    "CATEGORY_NOT_ALLOWED": 403,
    "QUOTE_INTEGRITY_FAILED": 403,
    "VELOCITY_LIMIT_EXCEEDED": 403,
    # platform
    "PAYMENT_SIGNATURE_INVALID": 400,
    "WEBHOOK_SIGNATURE_INVALID": 400,
    "RAZORPAY_ERROR": 502,
    # buyer
    "LLM_UNAVAILABLE": 503,
    "AGENT_LIMIT_REACHED": 409,
    "ILLEGAL_STATE_TRANSITION": 500,
    # demo control panel (§15). Not in the §18.2 table, which predates the
    # panel; these are the two answers a demo endpoint can give that are not
    # already covered above, and they use the same envelope as everything else.
    # DEMO_MODE_DISABLED is what every /api/demo/* endpoint returns when
    # DEMO_MODE is off, in place of a 404 that a caller could read as "the
    # deployment is broken" rather than "this is switched off on purpose".
    "DEMO_MODE_DISABLED": 403,
    "DEMO_PRECONDITION_FAILED": 409,
}

# The nine guard block codes, in rule order (§9.2).
GUARD_BLOCK_CODES: tuple[str, ...] = (
    "MANDATE_SIGNATURE_INVALID",
    "MANDATE_NOT_ACTIVE",
    "MANDATE_EXPIRED",
    "MERCHANT_NOT_ALLOWED",
    "AMOUNT_EXCEEDS_MANDATE",
    "CUMULATIVE_CAP_EXCEEDED",
    "CATEGORY_NOT_ALLOWED",
    "QUOTE_INTEGRITY_FAILED",
    "VELOCITY_LIMIT_EXCEEDED",
)

# Used when something escapes uncaught. Not in the §18.2 table; it means a bug.
INTERNAL_ERROR = "INTERNAL_ERROR"


class ErrorBody(BaseModel):
    code: str
    message: str
    correlation_id: str | None = None
    detail: dict[str, Any] | None = None


class ErrorEnvelope(BaseModel):
    error: ErrorBody


class KavachError(Exception):
    """Base class for every error this system raises deliberately.

    `code` must be a key of ERROR_STATUS; the HTTP status comes from there so a
    code cannot drift away from its status.
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        correlation_id: str | None = None,
        detail: dict[str, Any] | None = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.correlation_id = correlation_id
        self.detail = detail
        self.status_code = status_code or ERROR_STATUS.get(code, 500)


def error_response(
    code: str,
    message: str,
    *,
    correlation_id: str | None = None,
    detail: dict[str, Any] | None = None,
    status_code: int | None = None,
    request_id: str | None = None,
) -> JSONResponse:
    body = ErrorEnvelope(
        error=ErrorBody(
            code=code,
            message=message,
            correlation_id=correlation_id,
            detail=detail,
        )
    )
    headers = {"X-Request-Id": request_id} if request_id else None
    return JSONResponse(
        status_code=status_code or ERROR_STATUS.get(code, 500),
        content=body.model_dump(mode="json"),
        headers=headers,
    )


def _request_id(request: Request) -> str:
    # §18.3 — every endpoint accepts and echoes X-Request-Id, or generates one.
    return request.headers.get("x-request-id") or f"req_{uuid.uuid4().hex[:12]}"


def _correlation_id(request: Request) -> str | None:
    return (
        request.headers.get("x-correlation-id")
        or request.query_params.get("correlation_id")
        or getattr(request.state, "correlation_id", None)
    )


def install_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(KavachError)
    async def _kavach_error(request: Request, exc: KavachError) -> JSONResponse:
        return error_response(
            exc.code,
            exc.message,
            correlation_id=exc.correlation_id or _correlation_id(request),
            detail=exc.detail,
            status_code=exc.status_code,
            request_id=_request_id(request),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return error_response(
            "VALIDATION_ERROR",
            "Request body failed validation.",
            correlation_id=_correlation_id(request),
            detail={"errors": exc.errors()},
            status_code=422,
            request_id=_request_id(request),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        # A raw HTTPException may already carry a §18.2 code as its detail.
        detail = exc.detail
        code = detail if isinstance(detail, str) and detail in ERROR_STATUS else None
        return error_response(
            code or f"HTTP_{exc.status_code}",
            str(detail),
            correlation_id=_correlation_id(request),
            status_code=exc.status_code,
            request_id=_request_id(request),
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        # No secret and no stack detail crosses the boundary (§4.3).
        return error_response(
            INTERNAL_ERROR,
            "An unexpected error occurred.",
            correlation_id=_correlation_id(request),
            status_code=500,
            request_id=_request_id(request),
        )
