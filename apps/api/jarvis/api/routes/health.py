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


# ── A2A agent card ─────────────────────────────────────────────────────
# Discovery only. This publishes *what JARVIS can be asked to do*; it does not open a
# JSON-RPC task endpoint, and no card can grant a capability — every skill below still
# runs through the policy engine, and anything above R1 still needs a human approval.
# Generated from the policy table on purpose: a hand-written card drifts away from what
# the system actually permits, and a card that overstates the system is a lie a machine
# will act on.
_SKILL_TAGS = {
    "R0": ("read-only", "no-approval"),
    "R1": ("reversible", "owner-device"),
}


@router.get("/.well-known/agent-card.json")
async def agent_card() -> dict[str, Any]:
    """The A2A agent card (also served at the legacy ``/.well-known/agent.json``)."""
    from jarvis.services.policy.rules import RULES

    settings = get_settings()
    skills = [
        {
            "id": rule.tool,
            "name": rule.tool,
            "description": rule.description,
            "tags": list(_SKILL_TAGS[rule.risk.value]),
        }
        for rule in RULES.values()
        if rule.risk.value in _SKILL_TAGS
    ]

    return {
        "protocolVersion": "0.3.0",
        "name": "JARVIS X",
        "description": (
            "Autonomous personal operations platform. Observes commitments, predicts "
            "failure, prepares the next best action, executes through policy-controlled "
            "tools, verifies the result, and escalates only when authorized."
        ),
        "url": f"{settings.base_url}/v1",
        "version": BUILD_SHA,
        "provider": {"organization": "JARVIS X", "url": settings.base_url},
        "capabilities": {
            "streaming": True,
            "pushNotifications": True,
            "stateTransitionHistory": True,
        },
        "defaultInputModes": ["text/plain", "application/json"],
        "defaultOutputModes": ["text/plain", "application/json"],
        "securitySchemes": {
            "oauth2": {
                "type": "oauth2",
                "flows": {
                    "authorizationCode": {
                        "authorizationUrl": f"{settings.oauth_issuer}/oauth/authorize",
                        "tokenUrl": f"{settings.oauth_issuer}/oauth/token",
                        "scopes": {"tasks.read": "Read tasks", "tasks.write": "Create tasks"},
                    }
                },
            }
        },
        "security": [{"oauth2": ["tasks.read"]}],
        "skills": sorted(skills, key=lambda s: s["id"]),
        # Stated, not implied: effectful work exists but is not offered to a caller
        # without a human in the loop.
        "x-jarvis-approval-required-above": "R1",
    }


@router.get("/.well-known/agent.json")
async def agent_card_legacy() -> dict[str, Any]:
    return await agent_card()
