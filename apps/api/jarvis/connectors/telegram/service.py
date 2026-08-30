"""Telegram as an approval and escalation surface.

Chosen for Phase 1 because it is free, instant, needs no template approval, and supports
inline buttons — so the whole human-in-the-loop path is demonstrable before FCM, WhatsApp
or Alexa exist.

**Every surface is untrusted until its identity maps to a user** (blueprint §2). A
Telegram chat id is not an account; it is an ``identities`` row that points at one. An
unmapped chat is answered with nothing useful.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from jarvis.core.errors import Forbidden
from jarvis.core.logging import get_logger
from jarvis.db.models.agent import Action, Approval
from jarvis.db.models.identity import Identity
from jarvis.db.models.ops import AuditLog
from jarvis.services.tool_gateway import ToolGateway
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

log = get_logger(__name__)

PROVIDER = "telegram"
APPROVE_PREFIX = "approve:"
REJECT_PREFIX = "reject:"


@dataclass(frozen=True)
class CallbackOutcome:
    handled: bool
    text: str
    approval_id: uuid.UUID | None = None


class TelegramService:
    def __init__(self, session: AsyncSession, transport) -> None:  # noqa: ANN001
        self.session = session
        self.transport = transport

    # ── identity ───────────────────────────────────────────────────────
    async def link_chat(
        self, user_id: uuid.UUID, chat_id: str, *, is_owner: bool = True
    ) -> Identity:
        identity = Identity(
            user_id=user_id, provider=PROVIDER, subject=str(chat_id), is_owner=is_owner
        )
        self.session.add(identity)
        await self.session.flush()
        return identity

    async def user_for_chat(self, chat_id: str) -> uuid.UUID | None:
        """Map a chat id to an account, or ``None``.

        Returning ``None`` rather than raising is deliberate: an unmapped chat is an
        ordinary event — a stranger messaging the bot — not an error condition.
        """
        identity = await self.session.scalar(
            select(Identity).where(
                Identity.provider == PROVIDER,
                Identity.subject == str(chat_id),
                Identity.revoked_at.is_(None),
            )
        )
        return identity.user_id if identity else None

    # ── outbound ───────────────────────────────────────────────────────
    async def send_deadline_alert(
        self,
        chat_id: str,
        *,
        task_title: str,
        due_at: datetime,
        kind: str,
        probability: float | None = None,
        task_id: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        remaining = due_at - datetime.now(UTC)
        hours, minutes = divmod(max(0, int(remaining.total_seconds() // 60)), 60)
        when = f"{hours}h {minutes}m" if hours else f"{minutes}m"

        lines = [f"⏰ *{_escape(task_title)}*", f"Due in {when} · {kind}"]
        if probability is not None:
            lines.append(f"Completion probability: {probability:.0%}")

        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": "\n".join(lines),
            "parse_mode": "MarkdownV2",
        }
        if task_id is not None:
            payload["reply_markup"] = {
                "inline_keyboard": [[
                    {"text": "✅ Acknowledge", "callback_data": f"ack:{task_id}"},
                    {"text": "⏱ Snooze 30m", "callback_data": f"snooze:{task_id}"},
                ]]
            }
        return await self.transport.call("sendMessage", payload)

    async def send_approval_card(
        self, chat_id: str, *, approval: Approval, action: Action
    ) -> dict[str, Any]:
        """An approval card, showing what will happen before it happens.

        Recipients are stated explicitly: "send a message" and "send a message to your
        professor" are different decisions.
        """
        recipients = _recipients(action.args)
        lines = [
            f"🔐 *Approval needed* · risk {action.risk}",
            f"Action: `{_escape(action.tool)}`",
        ]
        if recipients:
            lines.append(f"To: {_escape(', '.join(recipients))}")
        if body := action.args.get("body") or action.args.get("text"):
            lines.append(f"\n_{_escape(str(body)[:300])}_")
        lines.append(f"\nExpires {approval.expires_at.strftime('%H:%M UTC')}")
        if approval.requires_local_confirmation:
            lines.append("⚠️ Also needs confirmation on your Mac")

        return await self.transport.call(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": "\n".join(lines),
                "parse_mode": "MarkdownV2",
                "reply_markup": {
                    "inline_keyboard": [[
                        {"text": "✅ Approve", "callback_data": f"{APPROVE_PREFIX}{approval.id}"},
                        {"text": "❌ Reject", "callback_data": f"{REJECT_PREFIX}{approval.id}"},
                    ]]
                },
            },
        )

    # ── inbound ────────────────────────────────────────────────────────
    async def handle_update(self, update: dict[str, Any]) -> CallbackOutcome:
        """Process one Telegram update.

        Only callback queries act. Free text is *not* a command channel in Phase 1 —
        an inline button carries an id we issued, whereas arbitrary text would need to be
        interpreted, and interpretation of untrusted input is what the policy boundary
        exists to keep away from effects.
        """
        callback = update.get("callback_query")
        if not callback:
            return CallbackOutcome(handled=False, text="")

        chat_id = str(callback.get("message", {}).get("chat", {}).get("id", ""))
        data = str(callback.get("data", ""))
        callback_id = callback.get("id")

        user_id = await self.user_for_chat(chat_id)
        if user_id is None:
            log.warning("telegram_unmapped_chat", chat_id=chat_id)
            await self._answer(callback_id, "This chat is not linked to a JARVIS account.")
            return CallbackOutcome(handled=False, text="unlinked chat")

        if data.startswith((APPROVE_PREFIX, REJECT_PREFIX)):
            return await self._decide(user_id, chat_id, callback_id, data)

        if data.startswith("ack:"):
            return await self._acknowledge(user_id, callback_id, data.removeprefix("ack:"))

        await self._answer(callback_id, "Unsupported action.")
        return CallbackOutcome(handled=False, text="unsupported")

    async def _decide(
        self, user_id: uuid.UUID, chat_id: str, callback_id: str, data: str
    ) -> CallbackOutcome:
        approved = data.startswith(APPROVE_PREFIX)
        raw = data.removeprefix(APPROVE_PREFIX if approved else REJECT_PREFIX)
        try:
            approval_id = uuid.UUID(raw)
        except ValueError:
            await self._answer(callback_id, "Malformed approval id.")
            return CallbackOutcome(handled=False, text="bad id")

        # The ownership check is in ToolGateway.decide, which scopes by user_id — so a
        # replayed callback_data from another account's card finds nothing.
        try:
            approval = await ToolGateway(self.session).decide(
                user_id, approval_id, approved=approved, decided_by="telegram"
            )
        except Exception as exc:  # noqa: BLE001
            log.info("telegram_decision_rejected", reason=type(exc).__name__)
            await self._answer(callback_id, "That approval is no longer available.")
            return CallbackOutcome(handled=False, text="unavailable")

        self.session.add(
            AuditLog(
                user_id=user_id, actor="telegram", action="approval.decided",
                subject_type="approval", subject_id=str(approval_id),
                detail={"decision": approval.decision, "chat_id": chat_id},
            )
        )
        message = "Approved ✅" if approved else "Rejected ❌"
        await self._answer(callback_id, message)
        await self.transport.call(
            "sendMessage", {"chat_id": chat_id, "text": f"{message} — recorded."}
        )
        return CallbackOutcome(handled=True, text=message, approval_id=approval_id)

    async def _acknowledge(
        self, user_id: uuid.UUID, callback_id: str, raw_task_id: str
    ) -> CallbackOutcome:
        from jarvis.services.goal import GoalService

        try:
            task_id = uuid.UUID(raw_task_id)
        except ValueError:
            await self._answer(callback_id, "Malformed task id.")
            return CallbackOutcome(handled=False, text="bad id")

        try:
            cancelled = await GoalService(self.session).acknowledge_task(user_id, task_id)
        except Exception:  # noqa: BLE001
            await self._answer(callback_id, "That task is no longer available.")
            return CallbackOutcome(handled=False, text="unavailable")

        await self._answer(callback_id, f"Acknowledged — {cancelled} later alert(s) cancelled.")
        return CallbackOutcome(handled=True, text="acknowledged")

    async def _answer(self, callback_id: str | None, text: str) -> None:
        if callback_id:
            await self.transport.call(
                "answerCallbackQuery", {"callback_query_id": callback_id, "text": text}
            )


def _recipients(args: dict[str, Any]) -> list[str]:
    found: list[str] = []
    for key in ("to", "recipient", "recipients", "channel"):
        value = args.get(key)
        if isinstance(value, str):
            found.append(value)
        elif isinstance(value, list):
            found.extend(str(v) for v in value)
    return found


_MARKDOWN_SPECIALS = r"_*[]()~`>#+-=|{}.!"


def _escape(text: str) -> str:
    """Escape MarkdownV2.

    Telegram rejects an unescaped special character with a 400, which would silently drop
    an alert about a deadline — the exact message that most needs to arrive.
    """
    return "".join(f"\\{c}" if c in _MARKDOWN_SPECIALS else c for c in str(text))


__all__ = ["CallbackOutcome", "Forbidden", "TelegramService"]
