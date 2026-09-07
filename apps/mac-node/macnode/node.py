"""The helper's connection loop.

One outbound WebSocket, reconnecting with backoff. No inbound port, no listening socket,
nothing to scan for.
"""

from __future__ import annotations

import asyncio
import json
import random
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from jarvis.core.logging import get_logger
from jarvis.services.device.protocol import JobEnvelope, MessageType

from macnode.adapters import MacAdapter, PyObjCAdapter
from macnode.executor import Executor
from macnode.guard import JobGuard, LocalPolicy

log = get_logger(__name__)

ACTIVITY_SAMPLE_SECONDS = 30
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
    # HTTP base for artifact uploads (screenshots, files). Empty = uploads disabled.
    api_http_url: str = ""
    # Post the frontmost app and window title every 30 s (PLAN.md 10.6.3). Off by
    # default; `python -m macnode run --share-activity` turns it on. Titles only.
    share_activity: bool = False


def make_uploader(api_http_url: str, access_token: str, device_id: str):  # noqa: ANN201
    """Multipart POST to the server's artifact endpoint, from the executor's thread."""
    import httpx

    def upload(path: str, *, kind: str, job_id: str) -> str | None:
        with open(path, "rb") as handle:
            response = httpx.post(
                f"{api_http_url}/v1/devices/{device_id}/artifacts",
                headers={"Authorization": f"Bearer {access_token}"},
                data={"kind": kind},
                files={"file": (path.rsplit("/", 1)[-1], handle)},
                timeout=120,
            )
        if response.status_code >= 400:
            log.warning("artifact_upload_failed", status=response.status_code, job_id=job_id)
            return None
        return str(response.json()["id"])

    return upload


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
            uploader=(
                make_uploader(config.api_http_url, config.access_token, config.device_id)
                if config.api_http_url
                else None
            ),
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
                    error=str(exc)[:200],
                    retry_in_seconds=round(delay, 1),
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
            sampler = (
                asyncio.create_task(self._sample_activity()) if self.config.share_activity else None
            )
            try:
                async for raw in socket:
                    await self._on_message(socket, json.loads(raw))
            finally:
                heartbeat.cancel()
                if sampler is not None:
                    sampler.cancel()

    async def _heartbeat(self, socket) -> None:  # noqa: ANN001
        while True:
            await asyncio.sleep(HEARTBEAT_SECONDS)
            await socket.send(json.dumps({"type": MessageType.DEVICE_HEARTBEAT.value}))

    async def _sample_activity(self) -> None:
        """Every 30 s: what is in front of the owner, as an app id and a window title.
        Batched and posted once a minute; never a pixel."""
        import httpx

        batch: list[dict[str, Any]] = []
        while True:
            await asyncio.sleep(ACTIVITY_SAMPLE_SECONDS)
            try:
                window = self.adapter.frontmost_window()
            except Exception as exc:  # noqa: BLE001
                log.debug("activity_sample_failed", error=str(exc)[:80])
                continue
            if window.frontmost_bundle_id:
                batch.append(
                    {
                        "app": window.frontmost_bundle_id,
                        "title": (window.window_title or "")[:300] or None,
                        "at": datetime.now(UTC).isoformat(),
                    }
                )
            if len(batch) < 2 or not self.config.api_http_url:
                continue
            try:
                response = await asyncio.to_thread(
                    httpx.post,
                    f"{self.config.api_http_url}/v1/devices/{self.config.device_id}/activity",
                    headers={"Authorization": f"Bearer {self.config.access_token}"},
                    json=batch,
                    timeout=20,
                )
                if response.status_code < 400:
                    batch.clear()
            except Exception as exc:  # noqa: BLE001 — keep sampling; post next time
                log.debug("activity_post_failed", error=str(exc)[:80])

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
                job_id=envelope.job_id,
                action=envelope.action,
                status=result.status,
            )
