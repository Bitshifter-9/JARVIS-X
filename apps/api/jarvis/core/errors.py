"""RFC 9457 problem details.

Errors are data, not prose. Every response carries a stable ``type`` a client can branch
on, and the ``correlation_id`` needed to find the request in the logs. Internal detail
never crosses the boundary — a 500 says so and nothing more.
"""

from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from jarvis.core.correlation import get_correlation_id
from jarvis.core.logging import get_logger

log = get_logger(__name__)

CONTENT_TYPE = "application/problem+json"
_BASE = "https://jarvis-x.dev/problems"


class ProblemError(Exception):
    """A failure with a client-meaningful shape."""

    def __init__(
        self,
        *,
        status: int,
        title: str,
        type_: str = "about:blank",
        detail: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        self.status = status
        self.title = title
        self.type = type_ if type_ == "about:blank" else f"{_BASE}/{type_}"
        self.detail = detail
        self.extra = extra or {}
        super().__init__(detail or title)


# ── the handful of failures the domain actually has ────────────────────
class NotFound(ProblemError):
    def __init__(self, resource: str, detail: str | None = None) -> None:
        super().__init__(
            status=404, title=f"{resource} not found", type_="not-found", detail=detail
        )


class Unauthorized(ProblemError):
    def __init__(self, detail: str = "Authentication required") -> None:
        super().__init__(status=401, title="Unauthorized", type_="unauthorized", detail=detail)


class Forbidden(ProblemError):
    def __init__(self, detail: str = "Not permitted") -> None:
        super().__init__(status=403, title="Forbidden", type_="forbidden", detail=detail)


class Conflict(ProblemError):
    def __init__(self, detail: str) -> None:
        super().__init__(
            status=409, title="Conflict", type_="conflict", detail=detail
        )


class PolicyDenied(ProblemError):
    """The policy engine refused.

    Distinct from Forbidden: the *action* is denied, not the caller.
    """

    def __init__(self, detail: str, risk: str | None = None) -> None:
        super().__init__(
            status=403, title="Action denied by policy", type_="policy-denied",
            detail=detail, extra={"risk": risk} if risk else None,
        )


class BudgetExceeded(ProblemError):
    def __init__(self, detail: str) -> None:
        super().__init__(
            status=429, title="Budget exceeded", type_="budget-exceeded", detail=detail
        )


class ProviderUnavailable(ProblemError):
    def __init__(self, detail: str) -> None:
        super().__init__(
            status=503, title="Upstream provider unavailable",
            type_="provider-unavailable", detail=detail,
        )


def _problem(status: int, title: str, type_: str, detail: str | None, extra: dict | None = None):
    body: dict[str, Any] = {"type": type_, "title": title, "status": status}
    if detail:
        body["detail"] = detail
    cid = get_correlation_id()
    if cid:
        body["correlation_id"] = cid
    if extra:
        body.update(extra)
    return JSONResponse(status_code=status, content=body, media_type=CONTENT_TYPE)


def register_exception_handlers(app) -> None:  # noqa: ANN001
    @app.exception_handler(ProblemError)
    async def _problem_error(_r: Request, exc: ProblemError):  # noqa: ANN202
        return _problem(exc.status, exc.title, exc.type, exc.detail, exc.extra)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_r: Request, exc: StarletteHTTPException):  # noqa: ANN202
        return _problem(exc.status_code, str(exc.detail), "about:blank", None)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_r: Request, exc: RequestValidationError):  # noqa: ANN202
        return _problem(
            422, "Request validation failed", f"{_BASE}/validation-error", None,
            {"errors": [
                {"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]} for e in exc.errors()
            ]},
        )

    @app.exception_handler(Exception)
    async def _unhandled(_r: Request, exc: Exception):  # noqa: ANN202
        # Log the detail; return none of it. The correlation id is how it gets found.
        log.exception("unhandled_exception", error=str(exc), error_type=type(exc).__name__)
        return _problem(500, "Internal server error", "about:blank", None)
