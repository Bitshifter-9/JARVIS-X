"""System status: real checks, not configuration flags.

"Is Gmail connected" is answered by refreshing the token; "is Telegram wired" by asking
Telegram who the bot is; "is the worker alive" by the age of its last pulse. Each check
is cheap and cached for a minute, so the app can show the card on every open.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from typing import Any

import httpx
from fastapi import APIRouter
from sqlalchemy import func, select, text

from jarvis.api.deps import CurrentUser, SessionDep
from jarvis.core.config import get_settings
from jarvis.db.models.job import Job
from jarvis.db.models.llm import ProviderHealth
from jarvis.db.models.ops import Device, WorkerHeartbeat
from jarvis.db.models.source import SourceAccount, SourceObject

router = APIRouter(prefix="/v1/system", tags=["system"])

_CACHE: dict[str, Any] = {"at": 0.0, "body": None}
CACHE_SECONDS = 60


def _check(
    name: str, ok: bool | None, detail: str, *, group: str, action: str | None = None
) -> dict[str, Any]:
    return {"name": name, "group": group, "ok": ok, "detail": detail[:300], "action": action}


async def _timed(coro, seconds: float = 6.0):  # noqa: ANN001, ANN202
    try:
        return await asyncio.wait_for(coro, timeout=seconds)
    except TimeoutError:
        return TimeoutError("timed out")
    except Exception as exc:  # noqa: BLE001
        return exc


async def run_checks(session, user_id) -> list[dict[str, Any]]:  # noqa: ANN001
    s = get_settings()
    checks: list[dict[str, Any]] = []
    now = datetime.now(UTC)

    # ── core ──
    try:
        await session.execute(text("SELECT 1"))
        checks.append(_check("Database", True, "Postgres reachable", group="core"))
    except Exception as exc:  # noqa: BLE001
        checks.append(_check("Database", False, str(exc), group="core"))

    pulses = {p.name: p for p in (await session.scalars(select(WorkerHeartbeat))).all()}
    expected = {
        "agent": 30,
        "notify": 60,
        "connector": s.gmail_poll_seconds + 120,
        "heartbeat": s.heartbeat_minutes * 60 + 120,
        "scheduler": s.scheduler_tick_seconds + 60,
    }
    for name, tolerance in expected.items():
        p = pulses.get(name)
        if p is None:
            checks.append(
                _check(
                    f"Worker · {name}",
                    False,
                    "never ticked — is `make worker` / `make scheduler` running?",
                    group="core",
                )
            )
            continue
        age = (now - p.last_tick_at).total_seconds()
        checks.append(
            _check(
                f"Worker · {name}", age <= tolerance, f"last tick {int(age)} s ago", group="core"
            )
        )
    pending = await session.scalar(
        select(func.count()).select_from(Job).where(Job.status == "pending")
    )
    dead = await session.scalar(select(func.count()).select_from(Job).where(Job.status == "dead"))
    checks.append(
        _check(
            "Job queue",
            (dead or 0) == 0,
            f"{pending or 0} pending · {dead or 0} dead-lettered",
            group="core",
        )
    )

    # ── models ──
    health = {h.provider: h for h in (await session.scalars(select(ProviderHealth))).all()}
    for name, key in (
        ("groq", s.groq_api_key),
        ("gemini", s.gemini_api_key),
        ("openrouter", s.openrouter_api_key),
    ):
        if not key:
            checks.append(
                _check(f"Model · {name}", None, "no key", group="models", action="settings")
            )
            continue
        h = health.get(name) or health.get(f"{name}_free")
        cooling = h is not None and h.cooldown_until is not None and h.cooldown_until > now
        detail = (
            f"key set · {h.total_calls} calls, {h.total_failures} failures"
            if h
            else "key set · not used yet"
        )
        if name == "groq":
            probe = await _timed(_groq_probe(key))
            if isinstance(probe, Exception):
                detail += f" · probe: {probe}"
                checks.append(
                    _check("Model · groq", False, detail, group="models", action="settings")
                )
                continue
            detail += " · key accepted" if probe else " · KEY REJECTED by Groq (401)"
            checks.append(
                _check("Model · groq", bool(probe), detail, group="models", action="settings")
            )
            continue
        checks.append(
            _check(
                f"Model · {name}",
                not cooling,
                detail + (" · cooling down" if cooling else ""),
                group="models",
            )
        )

    # ── connectors ──
    accounts = (
        await session.scalars(
            select(SourceAccount).where(
                SourceAccount.user_id == user_id, SourceAccount.revoked_at.is_(None)
            )
        )
    ).all()
    if not accounts:
        checks.append(
            _check(
                "Google",
                None,
                "no Google account connected — nothing is scanned",
                group="connectors",
                action="connect_google",
            )
        )
    for a in accounts:
        objects = await session.scalar(
            select(func.count()).select_from(SourceObject).where(SourceObject.account_id == a.id)
        )
        from jarvis.connectors.google.oauth import TokenStore

        token = await _timed(TokenStore(session).access_token(a.id))
        ok = not isinstance(token, Exception)
        when = (
            f"scanned {int((now - a.last_synced_at).total_seconds() // 60)} min ago"
            if a.last_synced_at
            else "never scanned yet (next poll runs it)"
        )
        result = a.last_sync_result or {}
        new_total = sum((result.get("new") or {}).values())
        seen_total = sum((result.get("seen") or {}).values())
        found = f", {new_total} new of {seen_total} seen last time" if result else ""
        token_state = "valid" if ok else "INVALID: " + str(token)[:80]
        detail = f"{a.external_id} · token {token_state} · {when}{found} · {objects} items stored"
        if a.last_error:
            detail += f" · error: {a.last_error[:120]}"
        checks.append(
            _check(
                f"Gmail · {a.external_id}",
                ok and not a.last_error,
                detail,
                group="connectors",
                action="sync",
            )
        )

    if s.telegram_bot_token:
        me = await _timed(_telegram_me(s.telegram_bot_token))
        checks.append(
            _check(
                "Telegram",
                not isinstance(me, Exception) and bool(me),
                f"bot @{me}" if isinstance(me, str) else f"getMe failed: {me}",
                group="connectors",
            )
        )
    else:
        checks.append(
            _check("Telegram", None, "no bot token", group="connectors", action="settings")
        )
    if s.slack_bot_token:
        team = await _timed(_slack_auth(s.slack_bot_token))
        checks.append(
            _check(
                "Slack",
                not isinstance(team, Exception) and bool(team),
                f"workspace {team}" if isinstance(team, str) else f"auth.test failed: {team}",
                group="connectors",
            )
        )
    else:
        checks.append(_check("Slack", None, "no bot token", group="connectors", action="settings"))
    checks.append(
        _check(
            "WhatsApp",
            None if not s.whatsapp_access_token else True,
            "configured" if s.whatsapp_access_token else "not configured",
            group="connectors",
            action=None if s.whatsapp_access_token else "settings",
        )
    )
    checks.append(
        _check(
            "Twilio",
            None if not s.twilio_account_sid else True,
            "configured" if s.twilio_account_sid else "not configured",
            group="connectors",
            action=None if s.twilio_account_sid else "settings",
        )
    )
    checks.append(
        _check(
            "Canvas",
            None if not s.canvas_api_token else True,
            "configured" if s.canvas_api_token else "not configured",
            group="connectors",
            action=None if s.canvas_api_token else "settings",
        )
    )
    checks.append(
        _check(
            "Alexa",
            None if not s.alexa_skill_id else True,
            "skill id set" if s.alexa_skill_id else "no skill id",
            group="connectors",
            action=None if s.alexa_skill_id else "settings",
        )
    )

    # ── push + voice ──
    if s.fcm_credentials_path:
        from jarvis.connectors.fcm import FcmSender

        loaded = await _timed(
            asyncio.to_thread(lambda: FcmSender(s.fcm_credentials_path, s.fcm_project_id)._load())
        )
        ok = not isinstance(loaded, Exception)
        client_ok = all((s.fcm_api_key, s.fcm_app_id, s.fcm_sender_id, s.fcm_project_id))
        checks.append(
            _check(
                "Push · FCM",
                ok and client_ok,
                ("service account ok" if ok else f"service account: {loaded}")
                + (
                    " · client values set"
                    if client_ok
                    else " · phone values (api key / app id / sender id / project id) MISSING"
                ),
                group="devices",
                action=None if client_ok else "settings",
            )
        )
    else:
        checks.append(
            _check("Push · FCM", None, "no service account", group="devices", action="settings")
        )
    checks.append(
        _check(
            "Wake word key",
            None if not s.picovoice_access_key else True,
            "Picovoice key set"
            if s.picovoice_access_key
            else "no Picovoice key — Always listening cannot start",
            group="devices",
            action=None if s.picovoice_access_key else "settings",
        )
    )

    # ── devices ──
    from jarvis.services.device import DeviceService

    devices = DeviceService(session)
    rows = [
        d
        for d in (await session.scalars(select(Device).where(Device.user_id == user_id))).all()
        if d.is_active
    ]
    if not rows:
        checks.append(_check("Devices", None, "nothing paired", group="devices", action="pair"))
    for d in rows:
        online = await devices.is_online(d.id)
        checks.append(
            _check(
                f"Device · {d.name} ({d.platform})",
                online,
                "online" if online else "paired, offline",
                group="devices",
            )
        )
    return checks


async def _groq_probe(key: str) -> bool:
    async with httpx.AsyncClient(timeout=6) as client:
        r = await client.get(
            "https://api.groq.com/openai/v1/models", headers={"Authorization": f"Bearer {key}"}
        )
    return r.status_code == 200


async def _telegram_me(token: str) -> str:
    async with httpx.AsyncClient(timeout=6) as client:
        r = await client.get(f"https://api.telegram.org/bot{token}/getMe")
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(data.get("description", "rejected"))
    return str(data["result"].get("username", "bot"))


async def _slack_auth(token: str) -> str:
    async with httpx.AsyncClient(timeout=6) as client:
        r = await client.post(
            "https://slack.com/api/auth.test", headers={"Authorization": f"Bearer {token}"}
        )
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(data.get("error", "rejected"))
    return str(data.get("team", "?"))


@router.get("/status")
async def status(user: CurrentUser, session: SessionDep, fresh: bool = False) -> dict[str, Any]:
    if not fresh and _CACHE["body"] is not None and time.time() - _CACHE["at"] < CACHE_SECONDS:
        return _CACHE["body"]
    checks = await run_checks(session, user.id)
    body = {
        "at": datetime.now(UTC).isoformat(),
        "ok": all(c["ok"] is not False for c in checks),
        "problems": sum(1 for c in checks if c["ok"] is False),
        "unconfigured": sum(1 for c in checks if c["ok"] is None),
        "checks": checks,
    }
    _CACHE.update(at=time.time(), body=body)
    return body
