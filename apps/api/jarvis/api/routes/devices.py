"""Device pairing and the outbound WebSocket the Mac dials in on.

No inbound port is ever opened on the Mac: it connects out, and every job it receives is
signed (blueprint §12).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from jarvis.api.deps import CurrentUser, SessionDep
from jarvis.core.config import get_settings
from jarvis.core.ids import new_id
from jarvis.core.logging import get_logger
from jarvis.core.security import decode_access_token
from jarvis.db.models.ops import Device
from jarvis.db.session import session_scope
from jarvis.services.device import DeviceService, MessageType, server_signing_key
from jarvis.services.device.keys import fingerprint
from jarvis.services.device.protocol import JobResult
from jarvis.services.evidence import EvidenceService

log = get_logger(__name__)
router = APIRouter(prefix="/v1/devices", tags=["devices"])


class PairStart(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    public_key_pem: str = Field(min_length=1)
    platform: str = Field(default="macos", max_length=24)
    allowed_bundle_ids: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)


class PairComplete(BaseModel):
    challenge: str
    signature: str


class DeviceOut(BaseModel):
    id: str
    name: str
    platform: str
    fingerprint: str
    paired: bool
    revoked: bool
    online: bool
    last_seen_at: str | None
    allowed_bundle_ids: list[str]
    capabilities: list[str] = Field(default_factory=list)
    last_location: dict[str, Any] | None = None


@router.post("/pair")
async def begin_pair(body: PairStart, user: CurrentUser, session: SessionDep) -> dict[str, Any]:
    """Register a public key and return a one-time challenge to sign.

    The device is not usable until it proves it holds the matching private half.
    """
    challenge = await DeviceService(session).begin_pairing(
        user.id,
        name=body.name,
        public_key_pem=body.public_key_pem,
        platform=body.platform,
        allowed_bundle_ids=body.allowed_bundle_ids,
        capabilities=body.capabilities,
    )
    return {
        "device_id": challenge.device_id,
        "challenge": challenge.challenge,
        "expires_at": challenge.expires_at.isoformat(),
        "fingerprint": fingerprint(body.public_key_pem),
    }


@router.post("/pair/complete", response_model=DeviceOut)
async def complete_pair(
    body: PairComplete, user: CurrentUser, session: SessionDep
) -> DeviceOut:
    devices = DeviceService(session)
    device = await devices.complete_pairing(
        user.id, challenge=body.challenge, signature=body.signature
    )
    return await _device_out(devices, device)


@router.get("", response_model=list[DeviceOut])
async def list_devices(user: CurrentUser, session: SessionDep) -> list[DeviceOut]:
    devices = DeviceService(session)
    return [await _device_out(devices, d) for d in await devices.list_devices(user.id)]


@router.post("/{device_id}/revoke", response_model=DeviceOut)
async def revoke(
    device_id: uuid.UUID, user: CurrentUser, session: SessionDep,
    reason: str = "revoked by user",
) -> DeviceOut:
    devices = DeviceService(session)
    device = await devices.revoke(user.id, device_id, reason=reason)
    return await _device_out(devices, device)


@router.websocket("/ws")
async def device_socket(websocket: WebSocket, token: str, device_id: str) -> None:
    """The Mac's outbound connection.

    Authorized on connect, exactly like the API Gateway route it replaces. The token is a
    short-lived access token; the device id must belong to the same account.
    """
    try:
        claims = decode_access_token(token)
        user_id = uuid.UUID(claims["sub"])
        device_uuid = uuid.UUID(device_id)
    except Exception:  # noqa: BLE001
        await websocket.close(code=4401)
        return

    connection_id = new_id("conn")
    async with session_scope() as session:
        devices = DeviceService(session)
        device = await session.get(Device, device_uuid)
        if device is None or device.user_id != user_id or not device.is_active:
            await websocket.close(code=4403)
            return
        await devices.connect(device_uuid, connection_id)

    await websocket.accept()
    log.info("device_connected", device_id=device_id, connection_id=connection_id)

    # On reconnect, tell the helper what is waiting — and what has gone stale and needs
    # an explicit decision rather than a late execution.
    async with session_scope() as session:
        dispatchable, needs_review = await DeviceService(session).pending_for_device(device_uuid)
        await websocket.send_json({
            "type": MessageType.SERVER_HELLO.value,
            "pending": len(dispatchable),
            "needs_review": [
                {"action_id": str(a.id), "tool": a.tool, "expired_at": a.expires_at.isoformat()}
                for a in needs_review
            ],
        })

    # Delivery, not just announcement. The executor addresses a Tier-B action to this
    # device from another process; every few seconds this socket looks for one and sends
    # the signed envelope. Waiting on receive alone would leave a queued job undelivered
    # until the helper happened to say something.
    try:
        while True:
            try:
                message = await asyncio.wait_for(
                    websocket.receive_json(), timeout=DISPATCH_POLL_SECONDS
                )
            except TimeoutError:
                await _push_dispatchable(websocket, device_uuid)
                continue
            await _handle_device_message(device_uuid, connection_id, message)
    except WebSocketDisconnect:
        pass
    finally:
        async with session_scope() as session:
            await DeviceService(session).disconnect(device_uuid, connection_id)
        log.info("device_disconnected", device_id=device_id)


DISPATCH_POLL_SECONDS = 3.0


async def _push_dispatchable(websocket: WebSocket, device_id: uuid.UUID) -> None:
    """Send every action the executor has addressed here and nobody has delivered yet."""
    from sqlalchemy import select

    from jarvis.db.models.agent import Action, ActionStatus
    from jarvis.services.device.service import server_signing_key

    async with session_scope() as session:
        devices = DeviceService(session)
        waiting = (
            await session.scalars(
                select(Action)
                .where(
                    Action.device_id == device_id,
                    Action.status == ActionStatus.DISPATCHED.value,
                    # ``result.job_id`` is written by build_envelope: its absence is the
                    # mark of a job that has not been sent.
                    Action.result.is_(None) | Action.result["job_id"].astext.is_(None),
                )
                .order_by(Action.created_at)
                .limit(5)
            )
        ).all()
        for action in waiting:
            envelope = await devices.build_envelope(action, server_private_pem=server_signing_key())
            await websocket.send_json(
                {"type": MessageType.JOB_DISPATCH.value, **envelope.to_wire()}
            )
            log.info("device_job_pushed", action_id=str(action.id), tool=action.tool)


async def _handle_device_message(
    device_id: uuid.UUID, connection_id: str, message: dict[str, Any]
) -> None:
    kind = message.get("type")

    if kind == MessageType.DEVICE_HEARTBEAT.value:
        async with session_scope() as session:
            await DeviceService(session).heartbeat(device_id, connection_id)
        return

    if kind == MessageType.JOB_RESULT.value:
        async with session_scope() as session:
            from sqlalchemy import select

            from jarvis.db.models.agent import Action
            from jarvis.db.models.ops import Device

            devices = DeviceService(session)
            device = await session.get(Device, device_id)
            result = JobResult.from_wire(message)

            # A device must not be able to declare its own success: the signature proves
            # the report came from the paired helper, and the verifier decides what it means.
            if device is None or not await devices.verify_result(device, result):
                log.warning("device_result_signature_invalid", device_id=str(device_id))
                return

            action = await session.scalar(
                select(Action).where(
                    Action.device_id == device_id,
                    Action.result["job_id"].astext == result.job_id,
                )
            )
            if action is None:
                log.warning("device_result_unknown_job", job_id=result.job_id)
                return

            await complete_device_action(session, action, result.observed or {})


async def _device_out(devices: DeviceService, device) -> DeviceOut:  # noqa: ANN001
    return DeviceOut(
        id=str(device.id),
        name=device.name,
        platform=device.platform,
        fingerprint=device.fingerprint[:16],
        paired=device.paired_at is not None,
        revoked=device.revoked_at is not None,
        online=await devices.is_online(device.id),
        last_seen_at=device.last_seen_at.isoformat() if device.last_seen_at else None,
        allowed_bundle_ids=device.allowed_bundle_ids,
        capabilities=device.capabilities,
        last_location=device.last_location,
    )


class AllowlistPatch(BaseModel):
    allowed_bundle_ids: list[str] | None = Field(default=None, max_length=200)
    capabilities: list[str] | None = Field(default=None, max_length=100)


@router.patch("/{device_id}/allowlist", response_model=DeviceOut)
async def edit_allowlist(
    device_id: uuid.UUID, body: AllowlistPatch, user: CurrentUser, session: SessionDep
) -> DeviceOut:
    """Edit what a device is allowed to do (FEATURES-50 #30). The server gate uses these,
    so tightening them here stops jobs from being dispatched to the device at all."""
    devices = DeviceService(session)
    device = await devices.get(user.id, device_id)
    if body.allowed_bundle_ids is not None:
        device.allowed_bundle_ids = [b[:200] for b in body.allowed_bundle_ids]
    if body.capabilities is not None:
        device.capabilities = [c[:64] for c in body.capabilities]
    await session.flush()
    return await _device_out(devices, device)


@router.get("/server-key")
async def server_public_key(_user: CurrentUser) -> dict[str, str]:
    """The server's public signing key, so a helper can verify the jobs it receives."""
    from cryptography.hazmat.primitives import serialization

    private = serialization.load_pem_private_key(server_signing_key().encode(), password=None)
    public_pem = private.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return {"public_key_pem": public_pem}


async def complete_device_action(session, action, observed: dict[str, Any]) -> dict[str, Any]:  # noqa: ANN001
    """What happens when a device reports back: the verdict, the artifact to Telegram,
    and — for the eyes (PLAN.md 10.7) — one description of what was captured."""
    from jarvis.db.models.ops import Artifact, AuditLog, Device
    from jarvis.services.vision import VISION_TOOLS, describe_artifact

    # Find-my-devices (#24): remember where a device reported it was.
    if action.tool == "phone.locate" and observed.get("lat") is not None and action.device_id:
        device = await session.get(Device, action.device_id)
        if device is not None and device.user_id == action.user_id:
            device.last_location = {
                "lat": observed.get("lat"),
                "lng": observed.get("lng"),
                "accuracy_m": observed.get("accuracy_m"),
                "at": datetime.now(UTC).isoformat(),
            }

    outcome = await EvidenceService(session).verify(action, observed)
    artifact_id = observed.get("artifact_id")
    if not artifact_id:
        return {"verdict": outcome.verdict.value}
    description = None
    if action.tool in VISION_TOOLS:
        try:
            artifact = await session.get(Artifact, uuid.UUID(str(artifact_id)))
        except ValueError:
            artifact = None
        if artifact is not None and artifact.user_id == action.user_id:
            description = await describe_artifact(
                session, action.user_id, artifact, (action.args or {}).get("question")
            )
            session.add(
                AuditLog(
                    user_id=action.user_id,
                    actor="system",
                    action="vision.described",
                    subject_type="action",
                    subject_id=str(action.id),
                    detail={
                        "tool": action.tool,
                        "artifact_id": str(artifact.id),
                        "text": description,
                    },
                    correlation_id=action.correlation_id,
                )
            )
            await session.flush()
            await _tell_owner(session, action.user_id, description)
    await deliver_artifact(session, action, str(artifact_id), caption=description)
    return {"verdict": outcome.verdict.value, "description": description}


async def _tell_owner(session, user_id, text: str) -> None:  # noqa: ANN001
    from jarvis.services.notification import NotificationService
    from jarvis.workers.notify import build_senders

    try:
        await NotificationService(session, senders=build_senders(session)).notify(
            user_id, title="Jarvis looked", body=text[:400]
        )
    except Exception as exc:  # noqa: BLE001 — the description is already recorded
        log.warning("vision_notify_failed", error=str(exc)[:120])


async def deliver_artifact(
    session, action, artifact_id: str, *, telegram=None, caption: str | None = None
) -> bool:  # noqa: ANN001
    """Send a screenshot or file the Mac produced to the owner's Telegram chat.

    The app's Timeline shows every artifact regardless; Telegram is the push. Only the
    owner's *linked* chat is ever a destination — an artifact is the most sensitive
    thing this system moves, and a recipient it never verified is not one it will use.
    """
    from sqlalchemy import select

    from jarvis.db.models.identity import Identity
    from jarvis.db.models.ops import Artifact

    try:
        artifact = await session.get(Artifact, uuid.UUID(artifact_id))
    except ValueError:
        return False
    if artifact is None or artifact.user_id != action.user_id:
        log.warning("artifact_delivery_refused", artifact_id=artifact_id)
        return False

    identity = await session.scalar(
        select(Identity).where(
            Identity.user_id == action.user_id, Identity.provider == "telegram",
            Identity.revoked_at.is_(None),
        )
    )
    settings = get_settings()
    chat_id = identity.subject if identity else settings.telegram_owner_chat_id
    if not chat_id:
        return False
    if telegram is None:
        if not settings.telegram_bot_token:
            return False
        from jarvis.connectors.telegram.client import TelegramClient

        telegram = TelegramClient(settings.telegram_bot_token)
    try:
        await telegram.send_file(
            chat_id, artifact.path,
            caption=(caption or f"{action.tool} · {artifact.filename}")[:1000],
            as_photo=artifact.content_type.startswith("image/"),
        )
    except Exception as exc:  # noqa: BLE001 — the artifact is stored either way
        log.warning("artifact_delivery_failed", error=str(exc)[:200])
        return False
    artifact.delivered_to = {**(artifact.delivered_to or {}), "telegram": chat_id}
    await session.flush()
    return True


# ── the notification mirror (PLAN.md 10.4.4) ─────────────────────────────────────────
class MirroredNotification(BaseModel):
    package: str = Field(max_length=200)
    app: str = Field(default="", max_length=120)
    title: str = Field(default="", max_length=300)
    # Empty unless the owner allowed bodies for this app on the phone.
    text: str = Field(default="", max_length=2000)
    at: str | None = None
    key: str | None = Field(default=None, max_length=200)


@router.post("/{device_id}/notifications", status_code=202)
async def mirror_notifications(
    device_id: uuid.UUID,
    body: list[MirroredNotification],
    user: CurrentUser,
    session: SessionDep,
) -> dict[str, Any]:
    """A paired phone forwards notification titles (opt-in, app-allowlisted on the
    phone). Each becomes an untrusted ``phone`` event — so a routine can say "when a
    WhatsApp from Amma arrives…" — and nothing else: no triage, no model call."""
    import hashlib
    from datetime import UTC, datetime

    from jarvis.core.correlation import ensure_correlation_id
    from jarvis.core.errors import Forbidden
    from jarvis.services.event import EventService
    from jarvis.services.event.envelope import EventEnvelope, EventSource, EventType, Trust
    from jarvis.services.routines import RoutineService

    device = await session.get(Device, device_id)
    if device is None or device.user_id != user.id or not device.is_active:
        raise Forbidden("That device is not paired to this account")

    events, routines = EventService(session), RoutineService(session)
    new = 0
    for n in body[:100]:
        digest = hashlib.sha256(f"{n.package}|{n.title}|{n.text}".encode()).hexdigest()[:12]
        object_id = n.key or f"{n.package}:{n.at or ''}:{digest}"
        try:
            occurred = datetime.fromisoformat(n.at) if n.at else datetime.now(UTC)
        except ValueError:
            occurred = datetime.now(UTC)
        author = n.app or n.package
        result = await events.ingest(
            EventEnvelope(
                event_type=EventType.SOURCE_MESSAGE_CHANGED,
                occurred_at=occurred,
                tenant_id=user.id,
                source=EventSource(provider="phone", object_id=object_id),
                correlation_id=ensure_correlation_id(),
                trust=Trust.UNTRUSTED,
                payload={
                    "kind": "notification",
                    "title": n.title,
                    "text": n.text or n.title,
                    "author": author,
                    "package": n.package,
                },
            )
        )
        if result.duplicate:
            continue
        new += 1
        await routines.on_message(
            user.id, provider="phone", author=author, title=n.title, body=n.text
        )
    return {"received": len(body), "new": new}


# ── the activity sampler (PLAN.md 10.6.3) ────────────────────────────────────────────
class ActivityIn(BaseModel):
    app: str = Field(max_length=200)
    title: str | None = Field(default=None, max_length=300)
    at: str | None = None


class ScreenIn(BaseModel):
    app: str = Field(default="", max_length=200)
    title: str | None = Field(default=None, max_length=300)
    text: str = Field(max_length=8000)  # the OCR'd text — never the screenshot (#1)
    at: str | None = None


class PhotoIn(BaseModel):
    caption: str = Field(default="", max_length=300)  # on-device caption/label
    text: str = Field(default="", max_length=4000)    # on-device OCR of the image
    uri: str | None = Field(default=None, max_length=1000)  # local content:// or file uri
    at: str | None = None


@router.post("/{device_id}/photo", status_code=202)
async def post_photo(
    device_id: uuid.UUID, body: list[PhotoIn], user: CurrentUser, session: SessionDep
) -> dict[str, Any]:
    """Photo & screenshot index (#8): a paired phone captions/OCRs each image *on-device* and
    posts only the derived text (never the pixels), stored as a searchable ``photo`` source so
    the camera roll becomes findable ("that receipt from Goa"). Opt-in, 30-day retention."""
    import hashlib
    from datetime import UTC, datetime, timedelta

    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from jarvis.core.errors import Forbidden
    from jarvis.core.ids import uuid7
    from jarvis.db.models.source import SourceObject

    device = await session.get(Device, device_id)
    if device is None or device.user_id != user.id or not device.is_active:
        raise Forbidden("That device is not paired to this account")

    now = datetime.now(UTC)
    retention = now + timedelta(days=30)
    rows = []
    seen: set[str] = set()
    for p in body[:120]:
        blob = f"{p.caption} {p.text}".strip()
        if not blob:
            continue
        try:
            at = datetime.fromisoformat(p.at) if p.at else now
        except ValueError:
            at = now
        oid = f"{device_id}:{hashlib.sha256((p.uri or blob).encode()).hexdigest()[:16]}"
        if oid in seen:
            continue
        seen.add(oid)
        rows.append({
            "id": uuid7(), "user_id": user.id, "provider": "photo",
            "object_id": oid, "kind": "photo",
            "title": (p.caption or "photo")[:300], "excerpt": blob[:4000],
            "url": p.uri, "occurred_at": at, "retention_until": retention,
        })
    if rows:
        stmt = pg_insert(SourceObject).values(rows).on_conflict_do_nothing(
            index_elements=["provider", "account_id", "object_id"]
        )
        await session.execute(stmt)
        await session.flush()
    return {"stored": len(rows)}


class TranscriptIn(BaseModel):
    text: str = Field(min_length=1, max_length=16000)
    kind: str = Field(default="ambient", pattern="^(ambient|meeting)$")  # #2 / #10
    title: str | None = Field(default=None, max_length=300)
    at: str | None = None


@router.post("/{device_id}/transcript", status_code=202)
async def post_transcript(
    device_id: uuid.UUID, body: TranscriptIn, user: CurrentUser, session: SessionDep
) -> dict[str, Any]:
    """Ambient audio (#2) / meeting capture (#10): a paired device posts the on-device
    *transcript* (the audio was transcribed locally and discarded). Stored as a searchable
    ``transcript`` source; a meeting also gets its dated action items turned into tasks."""
    import hashlib
    from datetime import UTC, datetime, timedelta

    from jarvis.core.errors import Forbidden
    from jarvis.db.models.source import SourceObject
    from jarvis.services.goal import GoalService

    device = await session.get(Device, device_id)
    if device is None or device.user_id != user.id or not device.is_active:
        raise Forbidden("That device is not paired to this account")

    now = datetime.now(UTC)
    try:
        at = datetime.fromisoformat(body.at) if body.at else now
    except ValueError:
        at = now
    digest = hashlib.sha256(body.text.encode()).hexdigest()[:16]
    title = body.title or ("Meeting" if body.kind == "meeting" else "Overheard")
    session.add(SourceObject(
        user_id=user.id, provider="voice", object_id=f"{device_id}:{digest}",
        kind="transcript", title=title[:300], excerpt=body.text[:16000],
        occurred_at=at, retention_until=now + timedelta(days=30),
        raw={"transcript_kind": body.kind},
    ))

    tasks_created = 0
    if body.kind == "meeting":
        # Turn dated action items in the meeting into tracked tasks (#10).
        from jarvis.services.extraction.regex_fallback import extract_deadline
        from jarvis.services.extraction.resolver import resolve

        tz = user.timezone or get_settings().timezone
        for line in body.text.split("."):
            line = line.strip()
            if len(line) < 6:
                continue
            guessed = extract_deadline(line, line, at, require_cue=True)
            if guessed is None or not guessed.has_deadline:
                continue
            try:
                r = resolve(guessed, received_at=at, default_timezone=tz)
            except Exception:  # noqa: BLE001
                r = None
            if r and r.due_at:
                await GoalService(session).create_task(
                    user.id, title=(guessed.title or line)[:500], due_at=r.due_at,
                    timezone=tz, evidence_span=line[:500], confidence=0.5,
                )
                tasks_created += 1
    await session.flush()
    return {"stored": 1, "tasks_created": tasks_created}


class HealthIn(BaseModel):
    day: str  # ISO date/datetime for the day
    steps: int | None = Field(default=None, ge=0)
    sleep_minutes: int | None = Field(default=None, ge=0)
    active_minutes: int | None = Field(default=None, ge=0)


@router.post("/{device_id}/health", status_code=202)
async def post_health(
    device_id: uuid.UUID, body: list[HealthIn], user: CurrentUser, session: SessionDep
) -> dict[str, Any]:
    """Energy/health (#38): a paired phone reports daily steps/sleep (from Health Connect),
    one row per day, correlated with your productivity. Opt-in."""
    from jarvis.core.errors import Forbidden
    from jarvis.services.health_metrics import record_health

    device = await session.get(Device, device_id)
    if device is None or device.user_id != user.id or not device.is_active:
        raise Forbidden("That device is not paired to this account")
    stored = await record_health(session, user.id, [s.model_dump() for s in body])
    return {"stored": stored}


class LocationIn(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    at: str | None = None


@router.post("/{device_id}/location", status_code=202)
async def post_location(
    device_id: uuid.UUID, body: list[LocationIn], user: CurrentUser, session: SessionDep
) -> dict[str, Any]:
    """Location trails (#5): a paired phone reports coarse fixes; the server rounds them to
    ~500 m before storing, so it is context, not a map. Opt-in, 30-day retention."""
    from jarvis.core.errors import Forbidden
    from jarvis.services.places import record_locations

    device = await session.get(Device, device_id)
    if device is None or device.user_id != user.id or not device.is_active:
        raise Forbidden("That device is not paired to this account")
    stored = await record_locations(session, user.id, [s.model_dump() for s in body])
    return {"stored": stored}


class ReadingIn(BaseModel):
    url: str = Field(max_length=2000)
    title: str | None = Field(default=None, max_length=400)
    kind: str = Field(default="article", max_length=16)  # article | video | pdf | page
    text: str | None = Field(default=None, max_length=8000)  # extracted content, optional
    at: str | None = None


@router.post("/{device_id}/reading", status_code=202)
async def post_reading(
    device_id: uuid.UUID, body: list[ReadingIn], user: CurrentUser, session: SessionDep
) -> dict[str, Any]:
    """Reading & watching log (#4): a paired device reports what you opened (article, video,
    PDF) — URL + title (+ extracted text where available) — stored as a life-searchable
    ``reading`` source, opt-in, 30-day retention. Feeds interest-drift (#17)."""
    import hashlib
    from datetime import UTC, datetime, timedelta

    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from jarvis.core.errors import Forbidden
    from jarvis.core.ids import uuid7
    from jarvis.db.models.source import SourceObject

    device = await session.get(Device, device_id)
    if device is None or device.user_id != user.id or not device.is_active:
        raise Forbidden("That device is not paired to this account")

    now = datetime.now(UTC)
    retention = now + timedelta(days=30)
    rows = []
    seen: set[str] = set()
    for r in body[:120]:
        url = (r.url or "").strip()
        if not url:
            continue
        try:
            at = datetime.fromisoformat(r.at) if r.at else now
        except ValueError:
            at = now
        # One row per URL per hour, so a tab you leave open isn't logged endlessly.
        bucket = at.strftime("%Y%m%d%H")
        oid = f"{hashlib.sha256(url.encode()).hexdigest()[:16]}:{bucket}"
        if oid in seen:
            continue
        seen.add(oid)
        rows.append({
            "id": uuid7(), "user_id": user.id, "provider": "reading",
            "object_id": oid, "kind": r.kind[:16] or "article",
            "title": (r.title or url)[:400],
            "excerpt": (r.text or r.title or url)[:8000], "url": url[:2000],
            "occurred_at": at, "retention_until": retention,
        })
    if rows:
        stmt = pg_insert(SourceObject).values(rows).on_conflict_do_nothing(
            index_elements=["provider", "account_id", "object_id"]
        )
        await session.execute(stmt)
        await session.flush()
    return {"stored": len(rows)}


@router.post("/{device_id}/screen", status_code=202)
async def post_screen(
    device_id: uuid.UUID, body: list[ScreenIn], user: CurrentUser, session: SessionDep
) -> dict[str, Any]:
    """Screen memory (#1): a paired Mac posts the OCR'd *text* of the active window (never a
    pixel), stored as a searchable ``screen`` source, opt-in, 30-day retention. Life-search
    reads it — Rewind-style recall without the disk cost. No task extraction."""
    import hashlib
    from datetime import UTC, datetime, timedelta

    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from jarvis.core.errors import Forbidden
    from jarvis.core.ids import uuid7
    from jarvis.db.models.source import SourceObject

    device = await session.get(Device, device_id)
    if device is None or device.user_id != user.id or not device.is_active:
        raise Forbidden("That device is not paired to this account")

    now = datetime.now(UTC)
    retention = now + timedelta(days=30)
    rows = []
    seen: set[str] = set()
    for s in body[:120]:
        text = (s.text or "").strip()
        if not text:
            continue
        try:
            at = datetime.fromisoformat(s.at) if s.at else now
        except ValueError:
            at = now
        digest = hashlib.sha256(f"{s.app}|{s.title}|{text}".encode()).hexdigest()[:16]
        oid = f"{device_id}:{digest}"
        if oid in seen:
            continue
        seen.add(oid)
        rows.append({
            "id": uuid7(), "user_id": user.id, "provider": "screen",
            "object_id": oid, "kind": "screen",
            "title": (s.title or s.app or "screen")[:300],
            "excerpt": text[:8000], "author": s.app or None,
            "occurred_at": at, "retention_until": retention,
        })
    if rows:
        # Duplicate identical windows are common — skip them, don't error.
        stmt = pg_insert(SourceObject).values(rows).on_conflict_do_nothing(
            index_elements=["provider", "account_id", "object_id"]
        )
        await session.execute(stmt)
        await session.flush()
    return {"stored": len(rows)}


@router.post("/{device_id}/activity", status_code=202)
async def post_activity(
    device_id: uuid.UUID, body: list[ActivityIn], user: CurrentUser, session: SessionDep
) -> dict[str, Any]:
    """A paired device reports what was in front of the owner: app and title, never
    pixels. Opt-in on the device; 30 days; the focus guard and "what was I doing" read it."""
    from jarvis.core.errors import Forbidden
    from jarvis.services.activity import record

    device = await session.get(Device, device_id)
    if device is None or device.user_id != user.id or not device.is_active:
        raise Forbidden("That device is not paired to this account")
    stored = await record(
        session,
        user.id,
        device_id=device.id,
        platform=device.platform,
        samples=[s.model_dump() for s in body],
    )
    return {"stored": stored}
