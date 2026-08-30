"""Persisting evidence and deciding whether an action actually worked."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from jarvis.core.ids import uuid7
from jarvis.core.logging import get_logger
from jarvis.db.models.agent import Action, ActionStatus, Evidence, Verdict
from jarvis.services.evidence.verifier import CheckResult, EvidenceRequirement, Verifier

log = get_logger(__name__)

# Keys never written into stored evidence, whatever a tool reports.
_REDACT_KEYS = ("token", "password", "secret", "authorization", "cookie", "api_key")


@dataclass(frozen=True)
class VerificationResult:
    verdict: Verdict
    checks: list[CheckResult]
    evidence_ids: list[uuid.UUID]

    @property
    def success(self) -> bool:
        return self.verdict is Verdict.VERIFIED

    @property
    def new_evidence(self) -> bool:
        """Whether anything was actually observed.

        The agent's reflect step repairs when there is new evidence and asks the user when
        there is not, so this distinction decides between trying again and giving up.
        """
        return any(c.observed is not None for c in self.checks)

    @property
    def summary(self) -> str:
        return "; ".join(f"{c.kind}={c.verdict.value}" for c in self.checks)


class EvidenceService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def verify(
        self, action: Action, observed: dict[str, Any], *, uri: str | None = None
    ) -> VerificationResult:
        """Compare an action's declared expectations against observed state, and record it."""
        requirements = [
            EvidenceRequirement(kind=e["kind"], value=e.get("value"))
            for e in (action.expected or [])
        ]
        results = Verifier.check_all(requirements, observed)
        verdict = Verifier.overall(results)

        redacted = _redact(observed)
        digest = "sha256:" + hashlib.sha256(repr(sorted(redacted.items())).encode()).hexdigest()
        now = datetime.now(UTC)

        ids: list[uuid.UUID] = []
        for result in results:
            row = Evidence(
                id=uuid7(),
                action_id=action.id,
                user_id=action.user_id,
                kind=result.kind,
                expected={"value": result.expected},
                observed={"value": result.observed, "detail": result.detail},
                verdict=result.verdict.value,
                uri=uri,
                digest=digest,
                redaction={"removed": _removed_keys(observed)} if _removed_keys(observed) else None,
                observed_at=now,
            )
            self.session.add(row)
            ids.append(row.id)

        action.status = (
            ActionStatus.SUCCEEDED.value if verdict is Verdict.VERIFIED
            else ActionStatus.FAILED.value
        )
        action.result = redacted
        await self.session.flush()

        log.info(
            "action_verified",
            action_id=str(action.id), tool=action.tool,
            verdict=verdict.value, checks=len(results),
        )
        return VerificationResult(verdict=verdict, checks=results, evidence_ids=ids)


def _redact(observed: dict[str, Any]) -> dict[str, Any]:
    """Strip secrets before storage.

    Redaction happens here rather than at each call site, because a call site that must
    remember to redact will eventually forget.
    """
    return {
        k: ("«redacted»" if any(s in k.lower() for s in _REDACT_KEYS) else v)
        for k, v in observed.items()
    }


def _removed_keys(observed: dict[str, Any]) -> list[str]:
    return [k for k in observed if any(s in k.lower() for s in _REDACT_KEYS)]
