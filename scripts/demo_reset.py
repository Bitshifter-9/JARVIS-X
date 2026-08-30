"""One-click demo reset.

Clears **only** the demo tenant's rows and re-seeds them. Refuses to run against a
non-local environment without an explicit override, because a reset script that can reach
production is a loaded gun in a drawer.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from jarvis.core.config import get_settings
from jarvis.db.base import Base
from jarvis.db.models.identity import User
from jarvis.db.session import dispose_engine, session_scope
from sqlalchemy import select, text

from scripts.seed_demo import EMAIL, seed


def tenant_tables() -> list[str]:
    """Every table carrying a ``user_id``, deepest dependency first.

    Derived from the schema rather than hardcoded: a hand-maintained list is precisely
    the thing that goes stale and leaves a table behind after the next migration.
    Join tables and children without a ``user_id`` (``task_dependencies``,
    ``entity_aliases``, ``device_connections``, ``connector_cursors``) cascade from their
    parents.
    """
    return [
        t.name
        for t in reversed(Base.metadata.sorted_tables)
        if "user_id" in t.c and t.name != "users"
    ]


async def reset(*, force: bool) -> int:
    settings = get_settings()
    if settings.env not in ("local", "test", "demo") and not force:
        print(
            f"refusing to reset in env={settings.env!r}; pass --force if you truly mean it",
            file=sys.stderr,
        )
        return 1

    tables = tenant_tables()

    async with session_scope() as session:
        user = await session.scalar(select(User).where(User.email == EMAIL))
        if user is None:
            print("no demo tenant to clear")
        else:
            for table in tables:
                await session.execute(
                    text(f'DELETE FROM "{table}" WHERE user_id = :uid'),  # noqa: S608
                    {"uid": user.id},
                )
            await session.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": user.id})
            print(f"cleared demo tenant {EMAIL} across {len(tables)} tables")

    await dispose_engine()
    await seed()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Reset the demo tenant")
    parser.add_argument("--force", action="store_true", help="allow outside local/demo")
    args = parser.parse_args()
    return asyncio.run(reset(force=args.force))


if __name__ == "__main__":
    raise SystemExit(main())
