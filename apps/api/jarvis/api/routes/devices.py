"""Device pairing and the outbound WebSocket the Mac dials in on.

No inbound port is ever opened on the Mac: it connects out, and every job it receives is
signed (blueprint §12).
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from jarvis.api.deps import CurrentUser, SessionDep
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

    try:
        while True:
            message = await websocket.receive_json()
            await _handle_device_message(device_uuid, connection_id, message)
    except WebSocketDisconnect:
        pass
    finally:
        async with session_scope() as session:
            await DeviceService(session).disconnect(device_uuid, connection_id)
        log.info("device_disconnected", device_id=device_id)


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

            await EvidenceService(session).verify(action, result.observed)


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
    )


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
