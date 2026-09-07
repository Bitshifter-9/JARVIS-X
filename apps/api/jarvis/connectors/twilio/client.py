"""Twilio voice — the last rung, and the only one that costs money per use.

Three properties the plan requires of it (§12, phase 5.5), and where each lives:

* **Once** — the escalation ladder advances one rung per attempt; it never re-dials.
* **Capped** — ``max_calls_per_day`` is enforced in ``notification/policy.py`` before a
  sender is ever reached, so the cap holds even if this class is called directly.
* **Opt-in and logged** — a call needs an enabled ``notification_endpoints`` row, and
  every send writes ``notification.sent``.

Phase 10.2 adds the *interactive* call: Twilio fetches TwiML from one of our webhook
URLs, gathers a spoken or keyed answer, and posts it back — signed. ``twilio_signature``
is the HMAC Twilio computes over the URL and the form fields; we verify it before an
answer can decide anything.

Amazon Connect is substituted here (PLAN.md §5): outbound Connect to India is restricted.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from collections.abc import Awaitable, Callable
from typing import Any
from xml.sax.saxutils import escape

import httpx
from jarvis.core.logging import get_logger

log = get_logger(__name__)
API_BASE = "https://api.twilio.com/2010-04-01"
VOICE = 'voice="Polly.Aditi" language="en-IN"'

Transport = Callable[[dict[str, str]], Awaitable[dict[str, Any]]]


def twiml_for(title: str, body: str) -> str:
    """The spoken script, as TwiML.

    Escaped, because the title comes from a task that may have been extracted from an
    email: an unescaped ``<`` would either break the call or inject markup into it.
    """
    spoken = f"{title}. {body}"
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f"<Response><Say {VOICE}>"
        f"{escape(spoken)}"
        "</Say><Pause length=\"1\"/><Say>This was an automated JARVIS escalation.</Say></Response>"
    )


def twiml_gather(prompt: str, *, action_url: str, fallback: str, hints: str = "") -> str:
    """Speak ``prompt`` and collect one keypress or a short utterance, posted to
    ``action_url``. ``fallback`` is spoken when nothing is gathered."""
    attr = {'"': "&quot;"}
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        f'<Gather input="speech dtmf" numDigits="1" action="{escape(action_url, attr)}" '
        f'method="POST" speechTimeout="auto" hints="{escape(hints, attr)}">'
        f"<Say {VOICE}>{escape(prompt)}</Say>"
        "</Gather>"
        f"<Say {VOICE}>{escape(fallback)}</Say>"
        "</Response>"
    )


def twiml_say(text: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f"<Response><Say {VOICE}>{escape(text)}</Say></Response>"
    )


def twilio_signature(auth_token: str, url: str, params: dict[str, str]) -> str:
    """What Twilio puts in ``X-Twilio-Signature``: HMAC-SHA1 over the full URL followed by
    every POST field, sorted by name, key then value, base64-encoded."""
    payload = url + "".join(f"{k}{params[k]}" for k in sorted(params))
    digest = hmac.new(auth_token.encode(), payload.encode(), hashlib.sha1).digest()  # noqa: S324 — Twilio's scheme
    return base64.b64encode(digest).decode()


def verify_twilio_signature(
    auth_token: str, url: str, params: dict[str, str], signature: str | None
) -> bool:
    if not signature:
        return False
    return hmac.compare_digest(twilio_signature(auth_token, url, params), signature)


class TwilioCaller:
    """Implements the notification sender contract: ``send(address, *, title, body)``,
    plus ``call(address, url=)`` for a call whose script lives at one of our URLs."""

    def __init__(
        self,
        account_sid: str,
        auth_token: str,
        from_number: str,
        *,
        timeout: float = 20.0,
        transport: Transport | None = None,
    ) -> None:
        self.account_sid = account_sid
        self.auth_token = auth_token
        self.from_number = from_number
        self.timeout = timeout
        self._transport = transport

    async def _post(self, data: dict[str, str]) -> dict[str, Any]:
        if not (self.account_sid and self.auth_token and self.from_number):
            raise RuntimeError("Twilio is not configured")
        if self._transport is not None:
            return await self._transport(data)
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{API_BASE}/Accounts/{self.account_sid}/Calls.json",
                auth=(self.account_sid, self.auth_token),
                data=data,
            )
        if response.status_code >= 400:
            log.warning("twilio_call_failed", status=response.status_code)
            raise RuntimeError(f"Twilio refused the call: {response.status_code}")
        data_out = response.json()
        log.info("twilio_call_placed", sid=data_out.get("sid"), status=data_out.get("status"))
        return data_out

    async def send(  # noqa: ANN001, ARG002
        self, address: str, *, title: str, body: str, task_id=None, data=None
    ) -> dict[str, Any]:
        return await self._post(
            {"To": address, "From": self.from_number, "Twiml": twiml_for(title, body)}
        )

    async def call(self, address: str, *, url: str) -> dict[str, Any]:
        """Ring ``address``; Twilio fetches the script from ``url`` and follows it."""
        return await self._post({"To": address, "From": self.from_number, "Url": url})
