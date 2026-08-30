"""Deadline extraction.

Accuracy is the graded metric (≥90%), so the techniques here are the ones that move it:
schema-constrained decoding, an exact-payload cache, and self-consistency voting reserved
for cases a single sample got unsure about.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from jarvis.core.logging import get_logger
from jarvis.llm import CallClass, LLMRequest, LLMRouter, Message
from jarvis.services.event.service import EventService
from jarvis.services.extraction.resolver import ResolutionError, resolve
from jarvis.services.extraction.schema import (
    DEADLINE_JSON_SCHEMA,
    ExtractedDeadline,
    ResolvedDeadline,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

log = get_logger(__name__)

PROMPT_VERSION = "extract/v3"
PROMPT_PATH = Path(__file__).resolve().parents[5] / "packages" / "prompts"
VOTE_BELOW_CONFIDENCE = 0.8
VOTE_SAMPLES = 3


@dataclass(frozen=True)
class ExtractionOutcome:
    resolved: ResolvedDeadline | None
    raw: ExtractedDeadline | None
    cached: bool = False
    voted: bool = False
    error: str | None = None


def _load_prompt() -> tuple[str, str]:
    source = (PROMPT_PATH / "deadline_extraction.md").read_text()
    blocks = re.findall(r"```\n(.*?)```", source, re.S)
    return blocks[0].strip(), blocks[1].strip()


def _cache_key(body: str, subject: str, received_at: datetime, timezone: str) -> str:
    payload = json.dumps(
        {
            "v": PROMPT_VERSION,
            "s": subject,
            "b": body,
            "r": received_at.isoformat(),
            "tz": timezone,
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


class ExtractionService:
    def __init__(self, session: AsyncSession, router: LLMRouter) -> None:
        self.session = session
        self.router = router

    async def extract(
        self,
        *,
        user_id: uuid.UUID,
        body: str,
        subject: str = "",
        sender: str = "",
        received_at: datetime,
        timezone: str,
    ) -> ExtractionOutcome:
        key = _cache_key(body, subject, received_at, timezone)
        if cached := await self._cache_get(key):
            raw = ExtractedDeadline(**cached)
            return ExtractionOutcome(
                resolved=self._resolve(raw, received_at, timezone), raw=raw, cached=True
            )

        system, template = _load_prompt()
        user_message = template.format(
            received_at=received_at.isoformat(),
            timezone=timezone,
            sender=sender or "unknown",
            subject=subject or "(none)",
            body=EventService.untrusted(body),
        )

        try:
            response = await self.router.generate(
                LLMRequest(
                    call_class=CallClass.EXTRACT,
                    messages=[Message("system", system), Message("user", user_message)],
                    json_schema=DEADLINE_JSON_SCHEMA,
                    temperature=0.0,
                    max_tokens=800,
                    prompt_version=PROMPT_VERSION,
                    user_id=user_id,
                )
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("extraction_failed", error=str(exc)[:200])
            return ExtractionOutcome(resolved=None, raw=None, error=str(exc)[:300])

        raw = self._parse(response.text)
        if raw is None:
            return ExtractionOutcome(resolved=None, raw=None, error="unparseable model output")

        voted = False
        if raw.has_deadline and raw.confidence < VOTE_BELOW_CONFIDENCE:
            # One unsure sample is a coin flip on the date. Costs n× output tokens, so it
            # is spent only where it changes the answer.
            agreed = await self._vote(system, user_message, user_id)
            if agreed is not None:
                raw, voted = agreed, True

        await self._cache_put(key, raw)
        return ExtractionOutcome(
            resolved=self._resolve(raw, received_at, timezone), raw=raw, voted=voted
        )

    async def _vote(
        self, system: str, user_message: str, user_id: uuid.UUID
    ) -> ExtractedDeadline | None:
        try:
            response = await self.router.generate(
                LLMRequest(
                    call_class=CallClass.EXTRACT,
                    messages=[Message("system", system), Message("user", user_message)],
                    json_schema=DEADLINE_JSON_SCHEMA,
                    temperature=0.6,
                    max_tokens=800,
                    prompt_version=f"{PROMPT_VERSION}+vote",
                    user_id=user_id,
                    samples=VOTE_SAMPLES,
                )
            )
        except Exception:  # noqa: BLE001
            return None

        parsed = [p for p in (self._parse(t) for t in response.samples) if p is not None]
        if not parsed:
            return None

        counts = Counter(p.due_at_local for p in parsed if p.has_deadline)
        if not counts:
            return None

        winner, votes = counts.most_common(1)[0]
        best = next(p for p in parsed if p.due_at_local == winner)
        agreement = votes / len(parsed)
        return best.model_copy(
            update={
                "confidence": max(best.confidence, agreement),
                "ambiguity": best.ambiguity if agreement < 0.67 else None,
            }
        )

    @staticmethod
    def _parse(raw_text: str) -> ExtractedDeadline | None:
        candidate = raw_text.strip()
        if candidate.startswith("```"):
            parts = candidate.split("```")
            candidate = parts[1] if len(parts) > 1 else candidate[3:]
            candidate = candidate.removeprefix("json").strip()
        try:
            return ExtractedDeadline(**json.loads(candidate))
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _resolve(
        raw: ExtractedDeadline, received_at: datetime, timezone: str
    ) -> ResolvedDeadline | None:
        try:
            return resolve(raw, received_at=received_at, default_timezone=timezone)
        except ResolutionError as exc:
            log.info("extraction_rejected", reason=str(exc))
            return None

    async def _cache_get(self, key: str) -> dict[str, Any] | None:
        row = await self.session.execute(
            text("SELECT content FROM extraction_cache WHERE cache_key = :k"), {"k": key}
        )
        found = row.scalar_one_or_none()
        return json.loads(found) if found else None

    async def _cache_put(self, key: str, extracted: ExtractedDeadline) -> None:
        await self.session.execute(
            text("""
                INSERT INTO extraction_cache (cache_key, prompt_version, content, created_at)
                VALUES (:k, :v, :c, now())
                ON CONFLICT (cache_key) DO NOTHING
            """),
            {"k": key, "v": PROMPT_VERSION, "c": extracted.model_dump_json()},
        )
