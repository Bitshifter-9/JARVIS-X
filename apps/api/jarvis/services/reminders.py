"""Disliked reminders: learn which sender/channel the user never wants nudged about (#14).

When the user dislikes a reminder we mute its source — a ``provider:author`` signature —
so the deadline pipeline stops turning that sender's mail/messages into tasks, and we
dismiss the ones already sitting in their list. The "Muted" section lists the signatures
so a mute can be undone. Free: a keyword/identity match, no model.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from jarvis.db.models.domain import ReminderMute, Task
from jarvis.db.models.source import SourceObject
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

_EMAIL = re.compile(r"[\w.+-]+@[\w.-]+\.\w+")


def _norm_author(provider: str, author: str | None) -> str:
    """The stable identity of a sender/channel. For mail, the address inside
    "Name <addr>"; otherwise the trimmed, lowercased author (or "" if unknown)."""
    a = (author or "").strip()
    if provider == "gmail":
        m = _EMAIL.search(a)
        if m:
            return m.group(0).lower()
    return a.lower()


def signature_for(provider: str | None, author: str | None) -> str | None:
    """``provider:author`` — the thing a mute suppresses. None if there's nothing to key on."""
    if not provider:
        return None
    who = _norm_author(provider, author)
    if not who:
        return None
    return f"{provider}:{who}"[:360]


def _label_for(provider: str | None, author: str | None) -> str:
    who = _norm_author(provider or "", author) or "unknown"
    nice = {"gmail": "Gmail", "slack": "Slack", "whatsapp": "WhatsApp",
            "telegram": "Telegram"}.get(provider or "", (provider or "source").title())
    return f"{nice} · {who}"[:360]


async def is_muted(session: AsyncSession, user_id: uuid.UUID,
                   provider: str | None, author: str | None) -> bool:
    """Has the user disliked reminders from this sender/channel?"""
    sig = signature_for(provider, author)
    if sig is None:
        return False
    hit = await session.scalar(
        select(ReminderMute.id).where(
            ReminderMute.user_id == user_id, ReminderMute.signature == sig
        )
    )
    return hit is not None


async def dislike_task(session: AsyncSession, user_id: uuid.UUID,
                       task_id: uuid.UUID) -> dict[str, Any]:
    """The user disliked a reminder: mute its source (so future ones are suppressed),
    then dismiss this task and every other open one from the same sender. A task with no
    source can only be dismissed — there is nothing to learn from."""
    task = await session.scalar(
        select(Task).where(Task.id == task_id, Task.user_id == user_id)
    )
    if task is None:
        return {"muted": False, "dismissed": 0, "reason": "not found"}

    source = await session.get(SourceObject, task.source_id) if task.source_id else None
    provider = source.provider if source else None
    author = source.author if source else None
    sig = signature_for(provider, author)

    dismissed = 0
    if sig is not None:
        mute = await session.scalar(
            select(ReminderMute).where(
                ReminderMute.user_id == user_id, ReminderMute.signature == sig
            )
        )
        if mute is None:
            session.add(ReminderMute(
                user_id=user_id, signature=sig, label=_label_for(provider, author),
                provider=provider, author=author,
            ))
        # Clear every open reminder already sitting in the list from this sender.
        siblings = (await session.scalars(
            select(Task).join(SourceObject, SourceObject.id == Task.source_id).where(
                Task.user_id == user_id,
                Task.status.in_(("open", "in_progress")),
                SourceObject.provider == provider,
            )
        )).all()
        for t in siblings:
            src = await session.get(SourceObject, t.source_id)
            if src and signature_for(src.provider, src.author) == sig:
                t.status = "cancelled"
                t.version += 1
                dismissed += 1
    else:
        task.status = "cancelled"
        task.version += 1
        dismissed = 1

    await session.flush()
    return {"muted": sig is not None, "signature": sig, "dismissed": dismissed}


async def list_mutes(session: AsyncSession, user_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = (await session.scalars(
        select(ReminderMute).where(ReminderMute.user_id == user_id)
        .order_by(ReminderMute.created_at.desc())
    )).all()
    return [{"id": str(m.id), "label": m.label, "provider": m.provider,
             "author": m.author, "created_at": m.created_at.isoformat()} for m in rows]


async def unmute(session: AsyncSession, user_id: uuid.UUID, mute_id: uuid.UUID) -> bool:
    m = await session.scalar(
        select(ReminderMute).where(ReminderMute.id == mute_id,
                                   ReminderMute.user_id == user_id)
    )
    if m is None:
        return False
    await session.delete(m)
    await session.flush()
    return True


if __name__ == "__main__":  # tiny self-check of the signature logic
    assert signature_for("gmail", "Promo <promo@brand.com>") == "gmail:promo@brand.com"
    assert signature_for("whatsapp", "News Channel") == "whatsapp:news channel"
    assert signature_for("gmail", None) is None
    assert signature_for(None, "x") is None
    print("ok")
