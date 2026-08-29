"""Liveness and readiness."""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter
from sqlalchemy import text

from jarvis.api.deps import SessionDep
from jarvis.core.config import get_settings
from jarvis.core.correlation import get_correlation_id

router = APIRouter(tags=["health"])

# Injected at build time. "dev" locally, so a response always identifies its build.
BUILD_SHA = os.environ.get("JARVIS_BUILD_SHA", "dev")


@router.get("/healthz")
async def healthz() -> dict[str, Any]:
    """Liveness. Deliberately touches nothing — a dependency outage is not a reason to
    restart this process."""
    return {
        "status": "ok",
        "build": BUILD_SHA,
        "env": get_settings().env,
        "correlation_id": get_correlation_id(),
    }


@router.get("/readyz")
async def readyz(session: SessionDep) -> dict[str, Any]:
    """Readiness. Checks the one dependency without which nothing works."""
    checks: dict[str, str] = {}
    try:
        await session.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:  # noqa: BLE001 — the point is to report, not to raise
        checks["database"] = f"error: {type(exc).__name__}"

    ready = all(v == "ok" for v in checks.values())
    return {"status": "ready" if ready else "degraded", "checks": checks, "build": BUILD_SHA}
