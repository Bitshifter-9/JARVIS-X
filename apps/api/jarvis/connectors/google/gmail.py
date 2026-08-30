"""Gmail ingestion.

Polls ``users.history.list`` on a stored cursor rather than using Pub/Sub push. Push needs
a GCP project, a verified domain and a topic subscription — days of setup to save 60
seconds of latency, on a demo path budgeted at 10 seconds end to end. The normalizer is
identical either way, so upgrading later touches only this file.

Message bodies are **untrusted data**. They reach the extractor inside a delimiter
envelope and can propose no tool call.
"""

from __future__ import annotations

import base64
import re
import uuid
from datetime import UTC, datetime

import httpx
from jarvis.connectors.base import ProviderEvidence, SyncItem, SyncPage
from jarvis.core.errors import Forbidden
from jarvis.core.logging import get_logger

log = get_logger(__name__)

API = "https://gmail.googleapis.com/gmail/v1"
MAX_BODY_CHARS = 12_000
_HTML_TAG = re.compile(r"<[^>]+>")
_QUOTED_LINE = re.compile(r"^\s*>.*$", re.M)
_SIGNATURE = re.compile(r"\n-- \n.*$", re.S)


class GmailConnector:
    provider = "gmail"

    def __init__(self, token_store) -> None:  # noqa: ANN001
        self.tokens = token_store

    async def _get(self, account_id: uuid.UUID, path: str, **params) -> dict:
        token = await self.tokens.access_token(account_id)
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(
                f"{API}{path}",
                headers={"Authorization": f"Bearer {token}"},
                params=params or None,
            )
        if response.status_code == 401:
            raise Forbidden("reauth_required: Gmail rejected the access token")
        if response.status_code == 404:
            return {}
        response.raise_for_status()
        return response.json()

    async def sync(self, account_id: uuid.UUID, cursor: str | None) -> SyncPage:
        """Fetch changes since ``cursor``.

        Without a cursor we take the current historyId and return nothing: a first
        connection must not import a decade of mail and manufacture a hundred deadlines.
        """
        if cursor is None:
            profile = await self._get(account_id, "/users/me/profile")
            return SyncPage(items=[], cursor=str(profile.get("historyId", "")), has_more=False)

        history = await self._get(
            account_id,
            "/users/me/history",
            startHistoryId=cursor,
            historyTypes="messageAdded",
            maxResults=100,
        )
        if not history:
            # Gmail expires old history ids; re-anchor rather than replay everything.
            profile = await self._get(account_id, "/users/me/profile")
            return SyncPage(items=[], cursor=str(profile.get("historyId", "")), has_more=False)

        message_ids: list[str] = []
        for record in history.get("history", []):
            for added in record.get("messagesAdded", []):
                message_id = added.get("message", {}).get("id")
                if message_id and message_id not in message_ids:
                    message_ids.append(message_id)

        items = [item for mid in message_ids if (item := await self.fetch(account_id, mid))]
        return SyncPage(
            items=items,
            cursor=str(history.get("historyId", cursor)),
            has_more=bool(history.get("nextPageToken")),
        )

    async def fetch(self, account_id: uuid.UUID, object_id: str) -> SyncItem | None:
        message = await self._get(account_id, f"/users/me/messages/{object_id}", format="full")
        if not message:
            return None
        return normalize(message)

    async def execute(
        self, account_id: uuid.UUID, action: str, args: dict
    ) -> ProviderEvidence:
        token = await self.tokens.access_token(account_id)
        if action == "gmail.create_draft":
            raw = _mime(args["to"], args.get("subject", ""), args["body"])
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.post(
                    f"{API}/users/me/drafts",
                    headers={"Authorization": f"Bearer {token}"},
                    json={"message": {"raw": raw}},
                )
            response.raise_for_status()
            data = response.json()
            return ProviderEvidence(object_id=data.get("id"), raw=data)

        if action == "gmail.send":
            raw = _mime(args["to"], args.get("subject", ""), args["body"])
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.post(
                    f"{API}/users/me/messages/send",
                    headers={"Authorization": f"Bearer {token}"},
                    json={"raw": raw},
                )
            response.raise_for_status()
            data = response.json()
            return ProviderEvidence(object_id=data.get("id"), raw=data)

        raise ValueError(f"gmail connector cannot perform {action}")

    async def revoke(self, account_id: uuid.UUID) -> None:
        account = await self.tokens.get_account(account_id)
        account.revoked_at = datetime.now(UTC)
        account.credentials = {}


def _mime(to: str, subject: str, body: str) -> str:
    message = f"To: {to}\r\nSubject: {subject}\r\n\r\n{body}"
    return base64.urlsafe_b64encode(message.encode()).decode()


def _headers(payload: dict) -> dict[str, str]:
    return {h["name"].lower(): h["value"] for h in payload.get("headers", [])}


def _decode(data: str) -> str:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding).decode("utf-8", errors="replace")


def _walk_parts(payload: dict) -> tuple[str, str]:
    """Return ``(plain, html)`` from a possibly nested MIME tree."""
    plain, html = "", ""
    mime = payload.get("mimeType", "")
    data = payload.get("body", {}).get("data")

    if data and mime == "text/plain":
        plain = _decode(data)
    elif data and mime == "text/html":
        html = _decode(data)

    for part in payload.get("parts", []):
        sub_plain, sub_html = _walk_parts(part)
        plain = plain or sub_plain
        html = html or sub_html
    return plain, html


def clean_body(plain: str, html: str) -> str:
    """Strip markup, quoted history and signatures.

    Quoted replies carry *old* deadlines. Leaving them in is a reliable way to extract a
    date that was superseded three messages ago.
    """
    text = plain or _HTML_TAG.sub(" ", html)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&").replace("&#39;", "'")
    text = _QUOTED_LINE.sub("", text)
    text = _SIGNATURE.sub("", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()[:MAX_BODY_CHARS]


def normalize(message: dict) -> SyncItem:
    payload = message.get("payload", {})
    headers = _headers(payload)
    plain, html = _walk_parts(payload)

    received_ms = message.get("internalDate")
    occurred_at = (
        datetime.fromtimestamp(int(received_ms) / 1000, tz=UTC) if received_ms else None
    )

    return SyncItem(
        provider="gmail",
        object_id=message["id"],
        kind="email",
        title=headers.get("subject", "(no subject)"),
        body=clean_body(plain, html),
        author=headers.get("from"),
        occurred_at=occurred_at,
        url=f"https://mail.google.com/mail/u/0/#inbox/{message['id']}",
        raw={"thread_id": message.get("threadId"), "labels": message.get("labelIds", [])},
    )
