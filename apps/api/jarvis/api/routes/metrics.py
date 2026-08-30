"""The metrics scorecard."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from jarvis.api.deps import CurrentUser, SessionDep
from jarvis.services.metrics import MetricsService

router = APIRouter(prefix="/v1/metrics", tags=["metrics"])


@router.get("")
async def scorecard(
    user: CurrentUser, session: SessionDep, window_days: int = 7, all_tenants: bool = False
) -> dict[str, Any]:
    """Measured values for the definition-of-done targets.

    ``all_tenants`` is for the operator's own view; it still only aggregates, and never
    returns another account's rows.
    """
    card = await MetricsService(session).scorecard(
        None if all_tenants else user.id, window_days=window_days
    )
    return {
        "generated_at": card.generated_at.isoformat(),
        "window_days": card.window_days,
        "headline": card.headline,
        "metrics": [
            {
                "key": m.key,
                "label": m.label,
                "value": m.value,
                "display": m.display,
                "target": m.target,
                "unit": m.unit,
                "sample_size": m.sample_size,
                "met": m.met,
            }
            for m in card.metrics
        ],
    }
