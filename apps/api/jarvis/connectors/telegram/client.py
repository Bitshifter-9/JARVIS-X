"""Telegram Bot API client.

Deliberately thin: send a message, answer a callback, edit a message. Anything richer
belongs in the service, where it can be tested without a network.
"""

from __future__ import annotations

from typing import Any, Protocol

import httpx
from jarvis.core.logging import get_logger

log = get_logger(__name__)
API_BASE = "https://api.telegram.org"


class TelegramTransport(Protocol):
    async def call(self, method: str, payload: dict[str, Any]) -> dict[str, Any]: ...


class TelegramClient:
    def __init__(self, bot_token: str, *, timeout: float = 10.0) -> None:
        self.bot_token = bot_token
        self.timeout = timeout

    async def call(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.bot_token:
            raise RuntimeError("JARVIS_TELEGRAM_BOT_TOKEN is not configured")
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{API_BASE}/bot{self.bot_token}/{method}", json=payload
            )
        data = response.json()
        if not data.get("ok"):
            log.warning("telegram_api_error", method=method, description=data.get("description"))
        return data


class RecordingTransport:
    """Captures calls instead of making them. Used by the tests."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((method, payload))
        return {"ok": True, "result": {"message_id": 100 + len(self.calls)}}

    def sent_texts(self) -> list[str]:
        return [p.get("text", "") for m, p in self.calls if m == "sendMessage"]
