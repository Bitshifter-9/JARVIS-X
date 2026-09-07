"""FCM HTTP v1: a push to the phone, signed with a service account.

No Firebase Admin SDK — it is one authenticated POST. The service account's OAuth token
comes from ``google-auth`` (already a dependency for Gmail), refreshed off the event loop
because its transport is synchronous.

The phone registers its token as a ``notification_endpoints`` row of channel ``push``;
the ladder tries this rung first because it is free, instant, and silent when the phone
is on Do Not Disturb — which is exactly why the rungs after it exist.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
from jarvis.core.logging import get_logger

log = get_logger(__name__)
SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
CHANNEL_ID = "jarvis_alerts"  # matches the Android notification channel the app creates


# Where a notification of each kind should open the app.
_ROUTE_FOR_KIND = {
    "deadline": "goals",
    "approval": "approvals",
    "agent": "timeline",
    "mail": "insights",
    "digest": "home",
    "commitment": "insights",
}


def message_payload(
    token: str,
    *,
    title: str,
    body: str,
    task_id: str | None = None,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The exact v1 body. Data *and* notification: data reaches the app in every state,
    notification lets Android show it even if the app process is dead. ``data`` may carry
    a ``kind`` and an entity ``id``; the app taps through to the matching screen."""
    extra = {str(k): str(v) for k, v in (data or {}).items() if v is not None}
    kind = extra.get("kind", "deadline")
    payload_data = {
        "task_id": task_id or "",
        "kind": kind,
        "route": extra.get("route", _ROUTE_FOR_KIND.get(kind, "home")),
        **extra,
    }
    return {
        "message": {
            "token": token,
            "notification": {"title": title[:200], "body": body[:1000]},
            "data": payload_data,
            "android": {
                "priority": "high",
                "notification": {"channel_id": CHANNEL_ID, "sound": "default"},
            },
        }
    }


class FcmSender:
    """Implements the notification sender contract: ``send(address, *, title, body)``."""

    def __init__(self, credentials: str, project_id: str = "", *, transport=None) -> None:  # noqa: ANN001
        self.credentials = credentials
        self._project_id = project_id
        self._creds = None
        self._transport = transport  # tests inject a callable(url, headers, json) → status

    def _load(self):  # noqa: ANN202
        from google.oauth2 import service_account

        raw = self.credentials.strip()
        if raw.startswith("{"):
            info = json.loads(raw)
            creds = service_account.Credentials.from_service_account_info(info, scopes=[SCOPE])
        else:
            info = json.loads(Path(raw).read_text())
            creds = service_account.Credentials.from_service_account_file(raw, scopes=[SCOPE])
        self._project_id = self._project_id or str(info.get("project_id", ""))
        return creds

    def _access_token(self) -> str:
        from google.auth.transport.requests import Request

        if self._creds is None:
            self._creds = self._load()
        if not self._creds.valid:
            self._creds.refresh(Request())
        return str(self._creds.token)

    async def send(  # noqa: ANN001
        self, address: str, *, title: str, body: str, task_id=None, data=None
    ) -> dict[str, Any]:
        payload = message_payload(
            address, title=title, body=body, task_id=str(task_id) if task_id else None, data=data
        )
        if self._transport is not None:
            status = await self._transport(payload)
            if status >= 400:
                raise RuntimeError(f"FCM rejected the message: {status}")
            return {"status": status}

        token = await asyncio.to_thread(self._access_token)
        url = f"https://fcm.googleapis.com/v1/projects/{self._project_id}/messages:send"
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                url, headers={"Authorization": f"Bearer {token}"}, json=payload
            )
        if response.status_code >= 400:
            log.warning("fcm_send_failed", status=response.status_code, body=response.text[:200])
            # UNREGISTERED means the token is dead; the ladder falls through to Telegram.
            raise RuntimeError(f"FCM rejected the message: {response.status_code}")
        return response.json()
