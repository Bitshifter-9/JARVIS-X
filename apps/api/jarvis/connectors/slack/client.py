"""Slack Web API client and request signing.

Thin on purpose, like the Telegram client: one ``call``, plus the signature check that
the webhook cannot be trusted without.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import Any, Protocol

import httpx
from jarvis.core.logging import get_logger
from jarvis.core.security import tokens_equal

log = get_logger(__name__)
API_BASE = "https://slack.com/api"

# Slack's own recommendation. A request older than this is a replay, however valid its
# signature: the signature never expires, so the timestamp is what bounds it.
MAX_SIGNATURE_AGE_SECONDS = 300


def verify_slack_signature(
    signing_secret: str,
    *,
    timestamp: str | None,
    body: bytes,
    signature: str | None,
    now: float | None = None,
) -> bool:
    """Verify ``X-Slack-Signature`` over ``v0:timestamp:body``.

    Returns a bool rather than raising: the caller answers 200 either way (a non-200
    makes Slack retry, and retrying a hostile request helps nobody), and only the
    verified path does any work.
    """
    if not signing_secret or not signature or not timestamp:
        return False
    try:
        sent_at = int(timestamp)
    except ValueError:
        return False
    if abs((now if now is not None else time.time()) - sent_at) > MAX_SIGNATURE_AGE_SECONDS:
        log.warning("slack_signature_stale", age_limit=MAX_SIGNATURE_AGE_SECONDS)
        return False

    expected = (
        "v0="
        + hmac.new(
            signing_secret.encode(),
            b"v0:" + timestamp.encode() + b":" + body,
            hashlib.sha256,
        ).hexdigest()
    )
    return tokens_equal(expected, signature)


class SlackTransport(Protocol):
    async def call(self, method: str, payload: dict[str, Any]) -> dict[str, Any]: ...


class SlackClient:
    def __init__(self, bot_token: str, *, timeout: float = 10.0) -> None:
        self.bot_token = bot_token
        self.timeout = timeout

    async def call(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.bot_token:
            raise RuntimeError("JARVIS_SLACK_BOT_TOKEN is not configured")
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{API_BASE}/{method}",
                json=payload,
                headers={"Authorization": f"Bearer {self.bot_token}"},
            )
        data = response.json()
        if not data.get("ok"):
            log.warning("slack_api_error", method=method, error=data.get("error"))
        return data


class RecordingSlackTransport:
    """Captures calls instead of making them. Used by the tests."""

    def __init__(self, *, ok: bool = True, channels=None, history=None) -> None:  # noqa: ANN001
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.ok = ok
        # For scan tests: {channel_id: [message dicts]} and a channel listing.
        self._channels = channels or []
        self._history = history or {}

    async def call(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((method, payload))
        if not self.ok:
            return {"ok": False, "error": "channel_not_found"}
        if method == "conversations.list":
            return {"ok": True, "channels": self._channels}
        if method == "conversations.history":
            return {"ok": True, "messages": self._history.get(payload.get("channel"), [])}
        return {
            "ok": True,
            "ts": f"1700000000.{len(self.calls):06d}",
            "channel": payload.get("channel"),
        }
