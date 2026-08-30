"""The helper's own admission control.

The server already decided this job is allowed. This checks again anyway, because the
server's decision travelled over a network to a process holding real macOS permissions.
Blueprint §12: the helper validates expiry, signature, nonce replay **and its own local
policy** before executing anything.

The local allowlist is the check that matters most. If the backend is ever wrong — a bug,
a compromise, a stale policy version — this is what stops it opening Terminal.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import UTC, datetime

from jarvis.services.device.keys import verify
from jarvis.services.device.protocol import JobEnvelope, RejectReason


@dataclass(frozen=True)
class GuardVerdict:
    accepted: bool
    reason: RejectReason | None = None
    detail: str = ""


class NonceLedger:
    """Remembers recently-seen nonces so a captured job cannot be replayed.

    Bounded, and ordered by insertion: a helper that ran for months would otherwise grow
    an unbounded set. Eviction is safe because every job also carries an expiry — a nonce
    old enough to be evicted belongs to a job that would be refused as expired anyway.
    """

    def __init__(self, capacity: int = 4096) -> None:
        self.capacity = capacity
        self._seen: OrderedDict[str, datetime] = OrderedDict()

    def seen(self, nonce: str) -> bool:
        return nonce in self._seen

    def remember(self, nonce: str) -> None:
        self._seen[nonce] = datetime.now(UTC)
        self._seen.move_to_end(nonce)
        while len(self._seen) > self.capacity:
            self._seen.popitem(last=False)

    def __len__(self) -> int:
        return len(self._seen)


@dataclass
class LocalPolicy:
    """What this helper will do, independent of what it is told."""

    allowed_bundle_ids: set[str] = field(default_factory=set)
    allowed_actions: set[str] = field(
        default_factory=lambda: {"mac.open_app", "mac.focus_app", "mac.run_template"}
    )
    allowed_templates: set[str] = field(default_factory=set)
    stopped: bool = False


class JobGuard:
    def __init__(self, *, server_public_pem: str, policy: LocalPolicy) -> None:
        self.server_public_pem = server_public_pem
        self.policy = policy
        self.nonces = NonceLedger()

    def admit(self, envelope: JobEnvelope, *, now: datetime | None = None) -> GuardVerdict:
        """Decide whether to run a job. Order matters: cheapest and most decisive first."""
        moment = now or datetime.now(UTC)

        # The menu-bar STOP wins over everything, including a valid signature.
        if self.policy.stopped:
            return GuardVerdict(False, RejectReason.STOPPED, "helper is stopped")

        if not verify(self.server_public_pem, envelope.signing_payload(), envelope.signature):
            return GuardVerdict(
                False, RejectReason.BAD_SIGNATURE, "job signature did not verify"
            )

        if envelope.is_expired(moment):
            return GuardVerdict(
                False, RejectReason.EXPIRED, f"job expired at {envelope.expires_at}"
            )

        if self.nonces.seen(envelope.nonce):
            return GuardVerdict(
                False, RejectReason.REPLAYED_NONCE, "this job has already been seen"
            )

        if envelope.action not in self.policy.allowed_actions:
            return GuardVerdict(
                False, RejectReason.UNKNOWN_ACTION, f"{envelope.action} is not enabled here"
            )

        if envelope.action in ("mac.open_app", "mac.focus_app"):
            bundle_id = envelope.args.get("bundle_id")
            if bundle_id not in self.policy.allowed_bundle_ids:
                return GuardVerdict(
                    False, RejectReason.NOT_ALLOWLISTED,
                    f"{bundle_id} is not in this Mac's allowlist",
                )

        if envelope.action == "mac.run_template":
            template = envelope.args.get("template")
            if template not in self.policy.allowed_templates:
                return GuardVerdict(
                    False, RejectReason.NOT_ALLOWLISTED,
                    f"template {template} is not enabled here",
                )

        # Remembered only once admitted, so a rejected job does not burn its own nonce
        # and mask a later legitimate retry.
        self.nonces.remember(envelope.nonce)
        return GuardVerdict(True)
