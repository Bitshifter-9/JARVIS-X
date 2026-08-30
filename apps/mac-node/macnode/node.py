"""The helper's connection loop.

One outbound WebSocket, reconnecting with backoff. No inbound port, no listening socket,
nothing to scan for.
"""

from __future__ import annotations

import asyncio
import json
import random
from dataclasses import dataclass, field
from typing import Any

from jarvis.core.logging import get_logger
from jarvis.services.device.protocol import JobEnvelope, MessageType

from macnode.adapters import MacAdapter, PyObjCAdapter
from macnode.executor import Executor
from macnode.guard import JobGuard, LocalPolicy

log = get_logger(__name__)

HEARTBEAT_SECONDS = 30
BACKOFF_CAP_SECONDS = 60.0


@dataclass
class NodeConfig:
    api_ws_url: str
    access_token: str
    device_id: str
    device_private_pem: str
    server_public_pem: str
    allowed_bundle_ids: set[str] = field(default_factory=set)
    allowed_templates: set[str] = field(default_factory=set)


class MacNode:
    def __init__(self, config: NodeConfig, adapter: MacAdapter | None = None) -> None:
        self.config = config
        self.policy = LocalPolicy(
            allowed_bundle_ids=set(config.allowed_bundle_ids),
            allowed_templates=set(config.allowed_templates),
        )
        self.executor = Executor(
            adapter=adapter or PyObjCAdapter(),
            guard=JobGuard(server_public_pem=config.server_public_pem, policy=self.policy),
            device_private_pem=config.device_private_pem,
        )

    def stop(self) -> None:
        """The menu-bar STOP. Outranks a valid signature; interrupts local work."""
        self.policy.stopped = True
        log.warning("mac_node_stopped")

    def resume(self) -> None:
        self.policy.stopped = False

    async def run_forever(self) -> None:
        attempt = 0
        while True:
            try:
                await self._session()
                attempt = 0
            except Exception as exc:  # noqa: BLE001 — a helper must survive the network
                attempt += 1
                delay = min(2**attempt, BACKOFF_CAP_SECONDS) * random.random()  # noqa: S311
                log.warning(
                    "mac_node_disconnected",
                    error=str(exc)[:200], retry_in_seconds=round(delay, 1),
                )
                await asyncio.sleep(delay)

    async def _session(self) -> None:
        import websockets

        url = (
            f"{self.config.api_ws_url}?token={self.config.access_token}"
            f"&device_id={self.config.device_id}"
        )
        async with websockets.connect(url) as socket:
            log.info("mac_node_connected", device_id=self.config.device_id)
            heartbeat = asyncio.create_task(self._heartbeat(socket))
            try:
                async for raw in socket:
                    await self._on_message(socket, json.loads(raw))
            finally:
                heartbeat.cancel()

    async def _heartbeat(self, socket) -> None:  # noqa: ANN001
        while True:
            await asyncio.sleep(HEARTBEAT_SECONDS)
            await socket.send(json.dumps({"type": MessageType.DEVICE_HEARTBEAT.value}))

    async def _on_message(self, socket, message: dict[str, Any]) -> None:  # noqa: ANN001
        kind = message.get("type")

        if kind == MessageType.SERVER_HELLO.value:
            # Anything stale is reported, never run late (blueprint §12).
            for item in message.get("needs_review", []):
                log.info("mac_node_stale_job_needs_review", **item)
            return

        if kind == MessageType.JOB_DISPATCH.value:
            envelope = JobEnvelope.from_wire(message)
            await socket.send(
                json.dumps({"type": MessageType.JOB_ACK.value, "job_id": envelope.job_id})
            )
            # Executed off the event loop: an app launch blocks, and a blocked loop
            # cannot answer a heartbeat or notice a STOP.
            result = await asyncio.to_thread(self.executor.handle, envelope)
            await socket.send(json.dumps(result.to_wire()))
            log.info(
                "mac_node_job_finished",
                job_id=envelope.job_id, action=envelope.action, status=result.status,
            )
