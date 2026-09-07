"""The two interactive calls (PLAN.md 10.2): approval by voice, and the wake-up call.

Every URL carries a token derived from the server secret and the row id, so a stranger
who guesses the id still cannot fetch the script; every answer is checked against
Twilio's signature before it decides anything; and "decide" is idempotent because the
tool gateway refuses to decide an approval twice.
"""

from __future__ import annotations

import hashlib
import hmac
import uuid

from jarvis.core.config import get_settings

APPROVE_WORDS = ("yes", "yeah", "yep", "approve", "approved", "go ahead", "do it", "ok", "okay")
REJECT_WORDS = ("no", "nope", "reject", "rejected", "don't", "do not", "stop", "cancel")


def call_token(kind: str, row_id: uuid.UUID | str) -> str:
    secret = get_settings().jwt_secret.encode()
    return hmac.new(secret, f"{kind}:{row_id}".encode(), hashlib.sha256).hexdigest()[:32]


def token_ok(kind: str, row_id: uuid.UUID | str, token: str) -> bool:
    return hmac.compare_digest(call_token(kind, row_id), token or "")


def approval_call_url(approval_id: uuid.UUID) -> str:
    base = get_settings().base_url.rstrip("/")
    return f"{base}/webhooks/twilio/approval/{approval_id}/{call_token('approval', approval_id)}"


def routine_call_url(routine_id: uuid.UUID) -> str:
    base = get_settings().base_url.rstrip("/")
    return f"{base}/webhooks/twilio/routine/{routine_id}/{call_token('routine', routine_id)}"


def interpret(digits: str | None, speech: str | None) -> bool | None:
    """1 / yes → True, 2 / no → False, anything else → None (ask again)."""
    if digits:
        return {"1": True, "2": False}.get(digits.strip())
    said = (speech or "").strip().lower()
    if not said:
        return None
    # "no" must win over "yes" in "no, don't approve" — check rejections first.
    if any(w in said for w in REJECT_WORDS):
        return False
    if any(w in said for w in APPROVE_WORDS):
        return True
    return None
