"""Personas (PLAN.md 10.3.2): a few presets, plus the user's own.

A persona is a fragment appended to the system prompt. It changes *voice and focus*,
never permissions: every persona hands actions to the same policy-gated agent.
"""

from __future__ import annotations

import re
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Any

from jarvis.services.profile import get_profile
from sqlalchemy.ext.asyncio import AsyncSession

# A library of role personas, one Markdown file each, in the agency-agents style
# (https://github.com/msitarzewski/agency-agents). Drop more .md files in this dir and
# they load automatically — with front matter `name:`/`description:` if present, else
# the first heading and paragraph are used, and the file body becomes the instructions.
_LIBRARY_DIR = Path(__file__).resolve().parents[1] / "data" / "personas"

PRESETS: dict[str, dict[str, str]] = {
    "jarvis": {
        "name": "Jarvis",
        "description": "The default: sharp, warm, direct.",
        "instructions": "",
    },
    "coach": {
        "name": "Coach",
        "description": "Pushes you to act. Short, energetic, ends with one next step.",
        "instructions": (
            "Take the voice of a demanding but kind coach. Be brief and energetic. Reframe "
            "problems as the next concrete action. End every reply with exactly one next "
            "step, phrased as an instruction. Never lecture."
        ),
    },
    "analyst": {
        "name": "Analyst",
        "description": "Structured, numbers first, trade-offs made explicit.",
        "instructions": (
            "Take the voice of a rigorous analyst. Lead with the conclusion, then the "
            "evidence. Prefer tables and numbered options with explicit trade-offs. Flag "
            "uncertainty with a probability when you can. No filler."
        ),
    },
    "study": {
        "name": "Study buddy",
        "description": "Explains, quizzes, and checks you understood.",
        "instructions": (
            "Take the voice of a patient study partner. Explain from first principles with "
            "one concrete example, then ask one short check question before moving on. "
            "When asked to quiz, ask one question at a time and grade the answer honestly."
        ),
    },
}

_SLUG = re.compile(r"[^a-z0-9]+")


def slug(name: str) -> str:
    return _SLUG.sub("-", name.strip().lower()).strip("-")[:40] or "persona"


def _parse_persona_md(key: str, text: str) -> dict[str, str]:
    """A library persona file → name/description/instructions.

    Accepts our `--- name:/description: ---` front matter and also a bare
    agency-agents file (no front matter): then the first `#` heading is the name, the
    first non-heading paragraph the description, and the whole file the instructions.
    """
    name = key.replace("-", " ").title()
    description = ""
    body = text.strip()
    if body.startswith("---"):
        _, fm, body = body.split("---", 2)
        for line in fm.splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                if k.strip() == "name":
                    name = v.strip() or name
                elif k.strip() == "description":
                    description = v.strip()
        body = body.strip()
    else:
        for line in body.splitlines():
            s = line.strip()
            if s.startswith("#"):
                name = s.lstrip("# ").strip() or name
            elif s and not description:
                description = s
        # keep the whole file as the instruction when there is no front matter
    return {
        "name": name[:60],
        "description": description[:200],
        "instructions": body[:2000],
    }


@lru_cache(maxsize=1)
def library() -> dict[str, dict[str, str]]:
    """Role personas loaded from the data dir, keyed by slug. Cached for the process."""
    out: dict[str, dict[str, str]] = {}
    if not _LIBRARY_DIR.is_dir():
        return out
    for path in sorted(_LIBRARY_DIR.glob("*.md")):
        key = slug(path.stem)
        if key in PRESETS or key in out:
            continue
        try:
            out[key] = _parse_persona_md(key, path.read_text(encoding="utf-8"))
        except OSError:
            continue
    return out


async def list_personas(session: AsyncSession, user_id: uuid.UUID) -> list[dict[str, Any]]:
    profile = await get_profile(session, user_id)
    rows = [{"key": k, "builtin": True, "group": "preset", **v} for k, v in PRESETS.items()]
    rows += [{"key": k, "builtin": True, "group": "library", **v} for k, v in library().items()]
    for key, spec in (profile.personas or {}).items():
        rows.append(
            {
                "key": key,
                "builtin": False,
                "group": "custom",
                "name": str(spec.get("name") or key),
                "description": str(spec.get("description") or ""),
                "instructions": str(spec.get("instructions") or ""),
            }
        )
    return rows


async def resolve(session: AsyncSession, user_id: uuid.UUID, key: str | None) -> str:
    """The prompt fragment for a persona key; empty for the default or an unknown key."""
    if not key or key == "jarvis":
        return ""
    if key in PRESETS:
        return PRESETS[key]["instructions"]
    if key in library():
        return library()[key]["instructions"]
    profile = await get_profile(session, user_id)
    spec = (profile.personas or {}).get(key) or {}
    return str(spec.get("instructions") or "")


async def save_persona(
    session: AsyncSession, user_id: uuid.UUID, key: str, *, name: str, instructions: str,
    description: str = "",
) -> dict[str, Any]:
    profile = await get_profile(session, user_id)
    personas = dict(profile.personas or {})
    personas[key] = {
        "name": name.strip()[:60] or key,
        "description": description.strip()[:200],
        "instructions": instructions.strip()[:2000],
    }
    profile.personas = personas
    await session.flush()
    return {"key": key, "builtin": False, **personas[key]}


async def delete_persona(session: AsyncSession, user_id: uuid.UUID, key: str) -> bool:
    profile = await get_profile(session, user_id)
    personas = dict(profile.personas or {})
    if key not in personas:
        return False
    del personas[key]
    profile.personas = personas
    await session.flush()
    return True
