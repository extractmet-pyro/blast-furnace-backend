"""Structured error model: every failure is explicit (never a disguised 200 OK)."""
from __future__ import annotations

from typing import Any, Optional

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class DomainError(Exception):
    """A request that is well-formed but violates a domain rule (plant envelope, mode, safety)."""

    def __init__(self, code: str, message: str, status_code: int = 409, details: Optional[Any] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details


class SolverError(DomainError):
    def __init__(self, message: str, details: Optional[Any] = None):
        super().__init__("SOLVER_FAILURE", message, 422, details)


def _body(code, message, request: Request, details=None):
    return {"error": {"code": code, "message": message, "details": details,
                      "request_id": getattr(request.state, "request_id", None), "path": request.url.path}}


async def domain_error_handler(request: Request, exc: DomainError):
    return JSONResponse(status_code=exc.status_code, content=_body(exc.code, exc.message, request, exc.details))


async def validation_error_handler(request: Request, exc: RequestValidationError):
    errs = [{"loc": list(e.get("loc", [])), "msg": e.get("msg"), "type": e.get("type")} for e in exc.errors()]
    return JSONResponse(status_code=422, content=_body("VALIDATION_ERROR", "Request failed validation", request, errs))


async def http_error_handler(request: Request, exc: StarletteHTTPException):
    code = {401: "UNAUTHORIZED", 403: "FORBIDDEN", 404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED",
            413: "PAYLOAD_TOO_LARGE", 429: "RATE_LIMITED"}.get(exc.status_code, "HTTP_ERROR")
    return JSONResponse(status_code=exc.status_code, content=_body(code, str(exc.detail), request),
                        headers=getattr(exc, "headers", None))


async def unhandled_error_handler(request: Request, exc: Exception):
    return JSONResponse(status_code=500, content=_body("INTERNAL_ERROR", "Unexpected server error (logged)", request))
