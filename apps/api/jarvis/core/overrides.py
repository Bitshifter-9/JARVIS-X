"""Settings the owner changed from the app, shared by every process.

``.env`` is what a container was *started* with. When the owner saves a key in the
app, the API process applies it live — but the worker is a different container with
its own copy of ``.env``, and both copies are replaced on the next deploy. So a saved
setting lives here, in ``settings_overrides``, and every process lays the rows over its
environment: the API at startup and after each save, the workers at the top of each
tick. One small SELECT, and "Settings" in the app means the same thing everywhere.

Locked fields (database URL, JWT secret, environment, device key) are never overridden:
those are what a process needs *before* it can read this table.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis.core.config import Settings, get_settings
from jarvis.core.logging import get_logger
from jarvis.db.models.ops import SettingOverride

log = get_logger(__name__)

LOCKED = frozenset({"database_url", "jwt_secret", "jwt_algorithm", "env", "device_signing_key_pem"})


def coerce(name: str, value: Any) -> Any:
    """The value as the field's declared type, via the same validation ``.env`` gets."""
    if name in LOCKED or name not in Settings.model_fields:
        raise KeyError(name)
    return getattr(Settings(**{name: value}), name)


async def apply_overrides(session: AsyncSession) -> int:
    """Lay the saved overrides over this process's settings. Returns how many applied."""
    settings = get_settings()
    rows = (await session.scalars(select(SettingOverride))).all()
    applied = 0
    for row in rows:
        if row.name in LOCKED or row.name not in Settings.model_fields:
            continue
        try:
            coerced = coerce(row.name, row.value)
        except Exception as exc:  # noqa: BLE001 — one bad row must not block the rest
            log.warning("setting_override_invalid", name=row.name, error=str(exc)[:120])
            continue
        if getattr(settings, row.name) != coerced:
            setattr(settings, row.name, coerced)
            applied += 1
    return applied


async def save_override(session: AsyncSession, name: str, value: Any) -> Any:
    """Persist one setting for every process and apply it here. Returns the coerced value."""
    coerced = coerce(name, value)
    stored = str(coerced).lower() if isinstance(coerced, bool) else str(coerced)
    await session.execute(
        pg_insert(SettingOverride)
        .values(name=name, value=stored)
        .on_conflict_do_update(index_elements=[SettingOverride.name], set_={"value": stored})
    )
    setattr(get_settings(), name, coerced)
    return coerced


async def clear_override(session: AsyncSession, name: str) -> None:
    row = await session.get(SettingOverride, name)
    if row is not None:
        await session.delete(row)
