"""Runtime settings: every ``JARVIS_*`` variable, editable from the apps.

The whole of ``Settings`` is exposed, grouped the way ``.env.example`` groups it, with
the current value of each field — the apps prefill from the running process, so what you
see is what is in force. Writes apply live and persist to ``.env`` so they survive a
restart. Single-tenant by design: whoever authenticates on this instance owns it.

Four fields are deliberately *not* editable here, because changing them from a running
app breaks the thing you are using to change them: the database URL, the JWT secret and
algorithm (every session would die mid-request), the environment name, and the device
signing key (every paired helper would refuse every job).

Secret values are returned in full to the authenticated owner — this is the owner reading
their own server, and the apps prefill the fields. The R4 rule guards the *agent* exporting
credentials, not the owner's UI.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Literal, get_args, get_origin

from fastapi import APIRouter
from pydantic import BaseModel

from jarvis.api.deps import CurrentUser, SessionDep
from jarvis.core.config import Settings, get_settings
from jarvis.core.errors import ProblemError
from jarvis.db.models.ops import AuditLog

router = APIRouter(prefix="/v1/settings", tags=["settings"])

LOCKED = frozenset({"database_url", "jwt_secret", "jwt_algorithm", "env", "device_signing_key_pem"})
_SECRET_MARKERS = ("key", "secret", "token", "password", "client_id", "auth_token", "sid")

# Prefix → section, in display order. Anything unmatched lands in "General".
SECTIONS: list[tuple[str, tuple[str, ...]]] = [
    (
        "LLM providers",
        ("groq_", "gemini_", "openrouter_", "ollama_", "cerebras_", "gateway_", "embedding_"),
    ),
    ("Budget", ("enable_paid_llm", "monthly_budget", "llm_budget", "max_")),
    ("Agent", ("scheduler_", "heartbeat_", "quiet_hours", "global_pause")),
    ("Focus guard", ("focus_",)),
    ("Owner", ("owner_",)),
    ("Google", ("google_", "gmail_")),
    ("Telegram", ("telegram_",)),
    ("Slack", ("slack_",)),
    ("WhatsApp", ("whatsapp_",)),
    ("Twilio", ("twilio_",)),
    ("Canvas", ("canvas_",)),
    ("Alexa", ("alexa_",)),
    ("OpenClaw", ("openclaw_",)),
    ("YouTube pipeline", ("youtube_", "pexels_", "huggingface_", "ffmpeg_", "r2_", "evidence_")),
    ("Voice", ("tts_",)),
    ("Devices", ("device_", "browser_")),
    ("Artifacts", ("artifact_",)),
    (
        "Server",
        (
            "base_url",
            "oauth_",
            "timezone",
            "log_level",
            "access_token",
            "refresh_token",
            "db_echo",
            "fcm_",
        ),
    ),
]

# Kept for the older app builds and the tests that read this shape.
SECRET_FIELDS = (
    "groq_api_key",
    "gemini_api_key",
    "openrouter_api_key",
    "pexels_api_key",
    "huggingface_api_key",
    "telegram_bot_token",
)
PLAIN_FIELDS = (
    "youtube_daily_topic",
    "youtube_daily_hour",
    "youtube_tts_voice",
    "youtube_privacy",
    "telegram_owner_chat_id",
)

ENV_PATH = Path(".env")
# Resolved once, at import: the display string for the apps, never touched per request.
ENV_FILE_DISPLAY = os.path.abspath(str(ENV_PATH))
_VALUE_OK = re.compile(r"[^\s#]{0,2000}$")  # env-file-safe: no whitespace, no comments


def is_secret(name: str) -> bool:
    return any(marker in name for marker in _SECRET_MARKERS)


def _section(name: str) -> str:
    for title, prefixes in SECTIONS:
        if any(name == p or name.startswith(p) for p in prefixes):
            return title
    return "General"


def _kind(annotation: Any) -> tuple[str, list[str] | None]:
    """A UI type for a field: bool, int, float, choice (with options), or text."""
    if annotation is bool:
        return "bool", None
    if annotation is int:
        return "int", None
    if annotation is float:
        return "float", None
    if get_origin(annotation) is Literal:
        return "choice", [str(v) for v in get_args(annotation)]
    return "text", None


def catalog() -> list[dict[str, Any]]:
    """Every editable field with its section, type, description and current value."""
    s = get_settings()
    rows: list[dict[str, Any]] = []
    for name, field in Settings.model_fields.items():
        if name in LOCKED:
            continue
        kind, options = _kind(field.annotation)
        rows.append(
            {
                "name": name,
                "env": f"JARVIS_{name.upper()}",
                "section": _section(name),
                "kind": kind,
                "options": options,
                "secret": is_secret(name),
                "description": field.description or "",
                "value": getattr(s, name),
                "default": field.default,
            }
        )
    order = {title: i for i, (title, _) in enumerate(SECTIONS)}
    rows.sort(key=lambda r: (order.get(r["section"], len(order)), r["name"]))
    return rows


class SettingsUpdate(BaseModel):
    model_config = {"extra": "allow"}


@router.get("")
async def read_settings(user: CurrentUser) -> dict[str, Any]:
    s = get_settings()
    return {
        "secrets": {name: bool(getattr(s, name)) for name in SECRET_FIELDS},
        "secret_values": {name: getattr(s, name) for name in SECRET_FIELDS},
        "values": {name: getattr(s, name) for name in PLAIN_FIELDS},
        "fields": catalog(),
        "locked": sorted(LOCKED),
        "env_file": ENV_FILE_DISPLAY,
    }


def _coerce(name: str, value: Any) -> Any:
    """Validate one value against the field's declared type. Bad input is a 400, not a
    500 on the next request that reads the setting."""
    field = Settings.model_fields[name]
    kind, options = _kind(field.annotation)
    try:
        if kind == "bool":
            if isinstance(value, bool):
                return value
            return str(value).strip().lower() in ("1", "true", "yes", "on")
        if kind == "int":
            number = int(value)
            if name == "youtube_daily_hour" and not 0 <= number <= 23:
                raise ProblemError(
                    status=400,
                    title="Invalid value",
                    type_="bad-request",
                    detail="youtube_daily_hour must be 0-23",
                )
            if number < 0 and name.startswith(("max_", "heartbeat_", "scheduler_")):
                raise ProblemError(
                    status=400,
                    title="Invalid value",
                    type_="bad-request",
                    detail=f"{name} must not be negative",
                )
            return number
        if kind == "float":
            return float(value)
    except (TypeError, ValueError):
        raise ProblemError(
            status=400,
            title="Invalid value",
            type_="bad-request",
            detail=f"{name} must be a {kind}",
        ) from None
    text = str(value if value is not None else "").strip()
    if kind == "choice" and text not in (options or []):
        raise ProblemError(
            status=400,
            title="Invalid value",
            type_="bad-request",
            detail=f"{name} must be one of {', '.join(options or [])}",
        )
    if "\n" in text or "#" in text or len(text) > 2000:
        raise ProblemError(
            status=400,
            title="Invalid value",
            type_="bad-request",
            detail=f"{name} may not contain newlines or '#'",
        )
    if is_secret(name) and not _VALUE_OK.match(text):
        raise ProblemError(
            status=400,
            title="Invalid value",
            type_="bad-request",
            detail=f"{name} contains whitespace or is too long",
        )
    return text


@router.put("")
async def update_settings(
    body: SettingsUpdate, user: CurrentUser, session: SessionDep
) -> dict[str, Any]:
    s = get_settings()
    updates: dict[str, str] = {}

    from jarvis.core.overrides import save_override

    for name, value in body.model_dump().items():
        if name in LOCKED or name not in Settings.model_fields:
            raise ProblemError(
                status=400,
                title="Unknown setting",
                type_="bad-request",
                detail=f"{name} is not configurable from the API",
            )
        coerced = _coerce(name, value)
        if name == "youtube_daily_hour":
            coerced = int(coerced)
        # The database is the source of truth every process reads; this process is
        # updated in the same call.
        await save_override(session, name, coerced)
        updates[name] = str(coerced).lower() if isinstance(coerced, bool) else str(coerced)

    if updates:
        # A .env file is worth keeping in step only where it is the developer's own
        # file; inside a container it is a copy that the next deploy replaces.
        if s.env in ("local", "test"):
            _persist_env(updates)
        session.add(
            AuditLog(
                user_id=user.id,
                actor="user",
                action="settings.updated",
                subject_type="settings",
                subject_id="env",
                # Names only. A value here would put a credential in the audit log.
                detail={"fields": sorted(updates)},
            )
        )
    return await read_settings(user)


def _persist_env(updates: dict[str, str]) -> None:
    """Update or append JARVIS_* lines in .env, preserving everything else — comments,
    ordering, and any line that is not ours."""
    lines = ENV_PATH.read_text().splitlines() if ENV_PATH.exists() else []
    for name, value in updates.items():
        env_name = f"JARVIS_{name.upper()}"
        for i, line in enumerate(lines):
            if line.startswith(f"{env_name}="):
                lines[i] = f"{env_name}={value}"
                break
        else:
            lines.append(f"{env_name}={value}")
    ENV_PATH.write_text("\n".join(lines) + "\n")
