"""Correlation id propagation.

One id follows a request across all nine hops: webhook -> event -> extraction -> task ->
schedule -> notification -> approval -> job -> evidence. If you cannot follow a single
request across all nine in the logs, the system is not done (PLAN.md §15).

The id lives in a ContextVar so it reaches log lines without being threaded through every
signature, and it survives ``await`` boundaries within a task.
"""

from __future__ import annotations

from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.types import ASGIApp

from jarvis.core.ids import new_correlation_id

_correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)

HEADER = "X-Correlation-ID"


def get_correlation_id() -> str | None:
    return _correlation_id.get()


def set_correlation_id(value: str) -> None:
    _correlation_id.set(value)


def ensure_correlation_id() -> str:
    """Return the current id, minting one if this is the start of a chain."""
    current = _correlation_id.get()
    if current is None:
        current = new_correlation_id()
        _correlation_id.set(current)
    return current


class CorrelationMiddleware(BaseHTTPMiddleware):
    """Adopt an inbound correlation id, or mint one, and echo it on the response.

    An inbound id is accepted only if it is well-formed, so a caller cannot inject
    arbitrary text into every downstream log line.
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next):  # noqa: ANN001, ANN201
        inbound = request.headers.get(HEADER)
        cid = inbound if inbound and _is_well_formed(inbound) else new_correlation_id()
        set_correlation_id(cid)
        request.state.correlation_id = cid
        response = await call_next(request)
        response.headers[HEADER] = cid
        return response


def _is_well_formed(value: str) -> bool:
    return (
        len(value) == 30
        and value.startswith("cor_")
        and all(c in "0123456789ABCDEFGHJKMNPQRSTVWXYZ" for c in value[4:])
    )
