"""Overnight agent report: what JARVIS prepared while you slept (#45)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from jarvis.api.deps import CurrentUser, SessionDep
from jarvis.services.overnight import last_report

router = APIRouter(prefix="/v1/overnight", tags=["overnight"])


@router.get("")
async def overnight(user: CurrentUser, session: SessionDep) -> dict[str, Any]:
    """The most recent overnight sweep — what it caught, derived and flagged for the morning."""
    return await last_report(session, user.id) or {}
