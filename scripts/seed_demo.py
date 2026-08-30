"""Seed a demo tenant: one user, one goal at risk, and its tasks.

Used by `make seed` and by the browser end-to-end check.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from jarvis.db.models.identity import User
from jarvis.db.session import dispose_engine, session_scope
from jarvis.services.goal import GoalService
from jarvis.services.identity import IdentityService
from sqlalchemy import select

EMAIL = "demo@jarvis-x.dev"
PASSWORD = "demo-password-12345"  # noqa: S105


async def seed() -> None:
    async with session_scope() as session:
        identity = IdentityService(session)
        existing = await session.scalar(select(User).where(User.email == EMAIL))
        user = existing or await identity.register(EMAIL, PASSWORD, display_name="Demo")
        await session.flush()

        goals = GoalService(session)
        if await goals.list_goals(user.id):
            print(f"already seeded: {EMAIL} / {PASSWORD}")
            return

        goal = await goals.create_goal(
            user.id,
            title="Hackathon submission",
            deadline=datetime.now(UTC) + timedelta(hours=3, minutes=20),
            timezone="Asia/Kolkata",
        )
        for title, minutes, optional in [
            ("Finish submission write-up", 150, False),
            ("Record demo video", 80, False),
            ("Alexa animation", 60, True),
            ("Knowledge-graph visualization", 70, True),
        ]:
            await goals.create_task(
                user.id, title=title, goal_id=goal.id,
                estimate_minutes=minutes, is_optional=optional,
            )
        print(f"seeded: {EMAIL} / {PASSWORD}")

    await dispose_engine()


if __name__ == "__main__":
    asyncio.run(seed())
