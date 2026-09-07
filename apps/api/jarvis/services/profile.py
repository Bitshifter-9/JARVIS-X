"""The profile: who the user is, rendered for every prompt.

Written by the user (Train tab), refined by what they correct (feedback), and — with
consent — by how they actually write (their own sent mail). Rendered once per turn as
standing context; facts learned later live in ``memories`` and are retrieved by
relevance. Empty sections are omitted so a new user costs no tokens.
"""

from __future__ import annotations

import uuid

from jarvis.db.models.ops import Profile
from sqlalchemy.ext.asyncio import AsyncSession

SECTIONS = (
    ("about", "About the user"),
    ("priorities", "What matters to them right now"),
    ("people", "People in their life and how to refer to them"),
    ("style", "How they want replies"),
    ("decisions", "How they make decisions"),
    ("learned_style", "How they write (learned from their own messages)"),
)


async def get_profile(session: AsyncSession, user_id: uuid.UUID) -> Profile:
    profile = await session.get(Profile, user_id)
    if profile is None:
        profile = Profile(user_id=user_id)
        session.add(profile)
        await session.flush()
        # The timestamps are server defaults; load them now rather than lazily later,
        # where the async session cannot.
        await session.refresh(profile)
    return profile


def render(profile: Profile | None) -> str:
    if profile is None:
        return ""
    parts = [
        f"{title}:\n{getattr(profile, key).strip()}"
        for key, title in SECTIONS
        if getattr(profile, key, "").strip()
    ]
    return (
        ("Who you are working for — treat this as ground truth:\n\n" + "\n\n".join(parts))
        if parts
        else ""
    )


async def profile_block(session: AsyncSession, user_id: uuid.UUID) -> str:
    from jarvis.services.triage import contacts_block

    parts = [render(await session.get(Profile, user_id)), await contacts_block(session, user_id)]
    return "\n\n".join(p for p in parts if p)
