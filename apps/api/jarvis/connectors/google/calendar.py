"""Google Calendar, read-only.

Feeds ``fixed_calendar_blocks`` into the prediction engine: a deadline three hours away
means something different when two of those hours are already a lecture.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import httpx
from jarvis.connectors.base import ProviderEvidence, SyncItem, SyncPage
from jarvis.core.errors import Forbidden
from jarvis.core.logging import get_logger

log = get_logger(__name__)
API = "https://www.googleapis.com/calendar/v3"


class CalendarConnector:
    provider = "gcal"

    def __init__(self, token_store) -> None:  # noqa: ANN001
        self.tokens = token_store

    async def _get(self, account_id: uuid.UUID, path: str, **params) -> dict:
        token = await self.tokens.access_token(account_id)
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(
                f"{API}{path}", headers={"Authorization": f"Bearer {token}"}, params=params or None
            )
        if response.status_code == 401:
            raise Forbidden("reauth_required: Calendar rejected the access token")
        response.raise_for_status()
        return response.json()

    async def sync(self, account_id: uuid.UUID, cursor: str | None) -> SyncPage:
        now = datetime.now(UTC)
        data = await self._get(
            account_id,
            "/calendars/primary/events",
            timeMin=now.isoformat(),
            timeMax=(now + timedelta(days=14)).isoformat(),
            singleEvents="true",
            orderBy="startTime",
            maxResults=250,
            **({"syncToken": cursor} if cursor else {}),
        )
        items = [
            normalize_event(e)
            for e in data.get("items", [])
            if e.get("status") != "cancelled"
        ]
        return SyncPage(items=items, cursor=data.get("nextSyncToken"), has_more=False)

    async def fetch(self, account_id: uuid.UUID, object_id: str) -> SyncItem | None:
        event = await self._get(account_id, f"/calendars/primary/events/{object_id}")
        return normalize_event(event) if event else None

    async def busy_minutes(
        self, account_id: uuid.UUID, *, start: datetime, end: datetime
    ) -> float:
        """Committed minutes between two instants, overlaps merged.

        Two meetings that overlap do not cost twice; counting them twice would make every
        forecast pessimistic in exactly the situations where the user is busiest.
        """
        token = await self.tokens.access_token(account_id)
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                f"{API}/freeBusy",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "timeMin": start.isoformat(),
                    "timeMax": end.isoformat(),
                    "items": [{"id": "primary"}],
                },
            )
        response.raise_for_status()
        busy = response.json().get("calendars", {}).get("primary", {}).get("busy", [])
        spans = [
            (datetime.fromisoformat(b["start"]), datetime.fromisoformat(b["end"])) for b in busy
        ]
        return merged_minutes(spans, start, end)

    async def execute(self, account_id: uuid.UUID, action: str, args: dict) -> ProviderEvidence:
        raise ValueError(f"calendar connector is read-only; cannot perform {action}")

    async def revoke(self, account_id: uuid.UUID) -> None:
        account = await self.tokens.get_account(account_id)
        account.revoked_at = datetime.now(UTC)
        account.credentials = {}


def merged_minutes(
    spans: list[tuple[datetime, datetime]], start: datetime, end: datetime
) -> float:
    clipped = sorted(
        (max(s, start), min(e, end)) for s, e in spans if min(e, end) > max(s, start)
    )
    total = 0.0
    current_start, current_end = None, None
    for span_start, span_end in clipped:
        if current_end is None or span_start > current_end:
            if current_end is not None:
                total += (current_end - current_start).total_seconds()
            current_start, current_end = span_start, span_end
        else:
            current_end = max(current_end, span_end)
    if current_end is not None:
        total += (current_end - current_start).total_seconds()
    return total / 60.0


def normalize_event(event: dict) -> SyncItem:
    start = event.get("start", {})
    when = start.get("dateTime") or start.get("date")
    occurred_at = datetime.fromisoformat(when) if when else None
    if occurred_at and occurred_at.tzinfo is None:
        occurred_at = occurred_at.replace(tzinfo=UTC)

    return SyncItem(
        provider="gcal",
        object_id=event["id"],
        kind="calendar_event",
        title=event.get("summary", "(untitled)"),
        body=event.get("description", ""),
        author=(event.get("organizer") or {}).get("email"),
        occurred_at=occurred_at,
        url=event.get("htmlLink"),
        raw={"end": event.get("end"), "all_day": "date" in start},
    )
