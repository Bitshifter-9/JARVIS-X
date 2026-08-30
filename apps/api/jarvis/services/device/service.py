"""Device pairing, job dispatch and the offline queue.

Pairing creates a device *identity*; it never copies cloud secrets to the Mac
(blueprint §12). The Mac then opens one outbound connection — no public port, no inbound
route, nothing to scan for.
"""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from jarvis.core.config import get_settings
from jarvis.core.errors import Conflict, Forbidden, NotFound
from jarvis.core.ids import new_job_id
from jarvis.core.logging import get_logger
from jarvis.db.models.agent import Action, ActionStatus
from jarvis.db.models.ops import AuditLog, Device, DeviceConnection
from jarvis.services.device.keys import fingerprint, sign, verify
from jarvis.services.device.protocol import JobEnvelope, JobResult
from jarvis.services.policy import POLICY_VERSION
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

log = get_logger(__name__)

PAIRING_CHALLENGE_TTL = timedelta(minutes=5)
# In-memory, because a pairing challenge is single-use and short-lived; losing them on
# restart costs one retry and avoids a table that would need sweeping.
_PENDING_CHALLENGES: dict[str, tuple[uuid.UUID, str, datetime]] = {}


@dataclass(frozen=True)
class PairingChallenge:
    device_id: str
    challenge: str
    expires_at: datetime


class DeviceService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ── pairing ────────────────────────────────────────────────────────
    async def begin_pairing(
        self,
        user_id: uuid.UUID,
        *,
        name: str,
        public_key_pem: str,
        platform: str = "macos",
        allowed_bundle_ids: list[str] | None = None,
        capabilities: list[str] | None = None,
    ) -> PairingChallenge:
        """Register a public key and hand back a one-time challenge to sign.

        The device is not usable until it proves it holds the matching private half —
        otherwise anyone could register someone else's public key and claim the pairing.
        """
        key_fingerprint = fingerprint(public_key_pem)

        existing = await self.session.scalar(
            select(Device).where(Device.fingerprint == key_fingerprint)
        )
        if existing is not None and existing.user_id != user_id:
            raise Conflict("That device key is already registered to another account")

        device = existing or Device(
            user_id=user_id,
            name=name,
            platform=platform,
            public_key_pem=public_key_pem,
            fingerprint=key_fingerprint,
            allowed_bundle_ids=allowed_bundle_ids or [],
            capabilities=capabilities or [],
        )
        device.name = name
        device.allowed_bundle_ids = allowed_bundle_ids or device.allowed_bundle_ids
        device.capabilities = capabilities or device.capabilities
        device.revoked_at = None
        self.session.add(device)
        await self.session.flush()

        challenge = secrets.token_urlsafe(32)
        expires_at = datetime.now(UTC) + PAIRING_CHALLENGE_TTL
        _PENDING_CHALLENGES[challenge] = (device.id, key_fingerprint, expires_at)

        log.info("device_pairing_started", device_id=str(device.id), name=name)
        return PairingChallenge(
            device_id=str(device.id), challenge=challenge, expires_at=expires_at
        )

    async def complete_pairing(
        self, user_id: uuid.UUID, *, challenge: str, signature: str
    ) -> Device:
        """Verify the signed challenge and mark the device paired."""
        entry = _PENDING_CHALLENGES.pop(challenge, None)
        if entry is None:
            raise Forbidden("Unknown or already-used pairing challenge")

        device_id, _key_fingerprint, expires_at = entry
        if expires_at <= datetime.now(UTC):
            raise Forbidden("Pairing challenge expired")

        device = await self.session.get(Device, device_id)
        if device is None or device.user_id != user_id:
            raise NotFound("Device")

        if not verify(device.public_key_pem, challenge.encode(), signature):
            log.warning("device_pairing_bad_signature", device_id=str(device_id))
            raise Forbidden("Challenge signature did not verify")

        device.paired_at = datetime.now(UTC)
        device.last_seen_at = datetime.now(UTC)
        self.session.add(
            AuditLog(
                user_id=user_id, actor="user", action="device.paired",
                subject_type="device", subject_id=str(device.id),
                detail={"name": device.name, "fingerprint": device.fingerprint[:16]},
            )
        )
        await self.session.flush()
        log.info("device_paired", device_id=str(device.id), name=device.name)
        return device

    async def revoke(self, user_id: uuid.UUID, device_id: uuid.UUID, *, reason: str) -> Device:
        device = await self._owned(user_id, device_id)
        device.revoked_at = datetime.now(UTC)
        self.session.add(
            AuditLog(
                user_id=user_id, actor="user", action="device.revoked",
                subject_type="device", subject_id=str(device.id), detail={"reason": reason},
            )
        )
        await self.session.flush()
        log.warning("device_revoked", device_id=str(device.id), reason=reason)
        return device

    async def list_devices(self, user_id: uuid.UUID) -> list[Device]:
        return list(
            (await self.session.scalars(select(Device).where(Device.user_id == user_id))).all()
        )

    # ── connections ────────────────────────────────────────────────────
    async def connect(self, device_id: uuid.UUID, connection_id: str) -> DeviceConnection:
        device = await self.session.get(Device, device_id)
        if device is None or not device.is_active or device.paired_at is None:
            raise Forbidden("Device is not paired or has been revoked")

        record = DeviceConnection(
            device_id=device_id, connection_id=connection_id, connected_at=datetime.now(UTC)
        )
        device.last_seen_at = datetime.now(UTC)
        self.session.add(record)
        await self.session.flush()
        return record

    async def heartbeat(self, device_id: uuid.UUID, connection_id: str) -> None:
        device = await self.session.get(Device, device_id)
        if device is not None:
            device.last_seen_at = datetime.now(UTC)
        record = await self.session.scalar(
            select(DeviceConnection).where(
                DeviceConnection.device_id == device_id,
                DeviceConnection.connection_id == connection_id,
                DeviceConnection.disconnected_at.is_(None),
            )
        )
        if record is not None:
            record.last_heartbeat_at = datetime.now(UTC)
        await self.session.flush()

    async def disconnect(self, device_id: uuid.UUID, connection_id: str) -> None:
        record = await self.session.scalar(
            select(DeviceConnection).where(
                DeviceConnection.device_id == device_id,
                DeviceConnection.connection_id == connection_id,
                DeviceConnection.disconnected_at.is_(None),
            )
        )
        if record is not None:
            record.disconnected_at = datetime.now(UTC)
            await self.session.flush()

    async def is_online(self, device_id: uuid.UUID) -> bool:
        record = await self.session.scalar(
            select(DeviceConnection).where(
                DeviceConnection.device_id == device_id,
                DeviceConnection.disconnected_at.is_(None),
            )
        )
        return record is not None

    # ── dispatch ───────────────────────────────────────────────────────
    async def build_envelope(self, action: Action, *, server_private_pem: str) -> JobEnvelope:
        """Turn an authorized action into a signed job for the helper."""
        if action.device_id is None:
            raise Conflict("This action is not addressed to a device")

        envelope = JobEnvelope(
            job_id=new_job_id(),
            action=action.tool,
            args=action.args,
            risk=action.risk,
            nonce=secrets.token_urlsafe(18),
            issued_at=datetime.now(UTC).isoformat(),
            expires_at=action.expires_at.isoformat(),
            policy_version=POLICY_VERSION,
            device_id=str(action.device_id),
        )
        signature = sign(server_private_pem, envelope.signing_payload())
        signed = JobEnvelope(**{**envelope.__dict__, "signature": signature})

        action.result = {"job_id": signed.job_id, "nonce": signed.nonce}
        await self.session.flush()
        return signed

    async def verify_result(self, device: Device, result: JobResult) -> bool:
        """Check the helper actually sent this result.

        Without it, anything that can reach the WebSocket could report a fabricated
        success for a job it never ran.
        """
        if not result.signature:
            return False
        return verify(device.public_key_pem, result.signing_payload(), result.signature)

    # ── offline behaviour (blueprint §12) ──────────────────────────────
    async def pending_for_device(self, device_id: uuid.UUID) -> tuple[list[Action], list[Action]]:
        """Return ``(dispatchable, needs_review)`` for a device that just reconnected.

        Considers actions that were authorized but never *delivered* — dispatch
        authorization happens at the moment of delivery, so an action addressed to an
        offline Mac is still ``approved`` rather than ``dispatched``.

        Jobs are **never silently executed once stale**. Anything past its expiry is
        marked expired and handed back for an explicit decision, because the intent that
        justified it may no longer hold.
        """
        rows = list(
            (
                await self.session.scalars(
                    select(Action)
                    .where(
                        Action.device_id == device_id,
                        Action.status.in_(
                            [ActionStatus.APPROVED.value, ActionStatus.AWAITING_APPROVAL.value]
                        ),
                    )
                    .order_by(Action.created_at)
                    # Re-read: expiry is a wall-clock fact, and a cached row from earlier
                    # in this transaction may predate it passing.
                    .execution_options(populate_existing=True)
                )
            ).all()
        )

        now = datetime.now(UTC)
        dispatchable: list[Action] = []
        needs_review: list[Action] = []

        for action in rows:
            if action.expires_at <= now:
                action.status = ActionStatus.EXPIRED.value
                needs_review.append(action)
                self.session.add(
                    AuditLog(
                        user_id=action.user_id, actor="system",
                        action="action.expired_while_offline",
                        subject_type="action", subject_id=str(action.id),
                        detail={"tool": action.tool, "expired_at": action.expires_at.isoformat()},
                    )
                )
            elif action.status == ActionStatus.APPROVED.value:
                dispatchable.append(action)
            else:
                needs_review.append(action)

        await self.session.flush()
        if needs_review:
            log.info(
                "device_reconnect_pending_review",
                device_id=str(device_id), stale=len(needs_review), ready=len(dispatchable),
            )
        return dispatchable, needs_review

    async def _owned(self, user_id: uuid.UUID, device_id: uuid.UUID) -> Device:
        device = await self.session.scalar(
            select(Device).where(Device.id == device_id, Device.user_id == user_id)
        )
        if device is None:
            raise NotFound("Device")
        return device


def server_signing_key() -> str:
    """The server's private signing key.

    Generated on demand in local development so ``make api`` works out of the box; in any
    other environment a missing key is a hard failure, because a per-restart key would
    silently invalidate every paired helper.
    """
    settings = get_settings()
    if settings.device_signing_key_pem:
        return settings.device_signing_key_pem
    if settings.env in ("local", "test"):
        global _DEV_KEY
        if _DEV_KEY is None:
            from jarvis.services.device.keys import generate_keypair

            _DEV_KEY = generate_keypair()[0]
        return _DEV_KEY
    raise RuntimeError(
        "JARVIS_DEVICE_SIGNING_KEY_PEM is not set. Generate one with "
        "`python -m jarvis.services.device.keys` and store it in your secret manager."
    )


_DEV_KEY: str | None = None
