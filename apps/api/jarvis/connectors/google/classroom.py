"""Google Classroom, read-only (PLAN.md phase 5.2).

Coursework is the cleanest deadline source there is: the due date is a structured field,
not a sentence to extract from. When Classroom is connected the extraction model is not
consulted at all for these items — a parsed field beats a 90%-accurate inference.

Classroom needs institutional authorization the demo tenant may not have (§13), so the
connector is written to the same interface as Canvas: whichever one is available feeds
the same tasks.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import httpx
from jarvis.connectors.base import ProviderEvidence, SyncItem, SyncPage
from jarvis.core.errors import Forbidden
from jarvis.core.logging import get_logger

log = get_logger(__name__)
API = "https://classroom.googleapis.com/v1"
PROVIDER = "classroom"


def coursework_due_at(work: dict) -> datetime | None:
    """Classroom's split ``dueDate``/``dueTime`` → one aware UTC instant.

    ``dueTime`` is absent for a whole-day deadline, which Classroom means as *end* of
    that day. Treating a missing time as midnight would move every such deadline a full
    day earlier and fire every reminder against the wrong day.
    """
    date = work.get("dueDate")
    if not date:
        return None
    time_of_day = work.get("dueTime") or {}
    if time_of_day:
        hour, minute = time_of_day.get("hours", 0), time_of_day.get("minutes", 0)
    else:
        hour, minute = 23, 59
    try:
        return datetime(
            date["year"], date["month"], date["day"], hour, minute, tzinfo=UTC
        )
    except (KeyError, ValueError):
        log.warning("classroom_unparseable_due_date", raw=str(date)[:120])
        return None


def normalize_coursework(work: dict, *, course_name: str | None = None) -> SyncItem:
    return SyncItem(
        provider=PROVIDER,
        object_id=f"{work.get('courseId', '')}:{work['id']}",
        kind="coursework",
        title=work.get("title", "(untitled coursework)"),
        body=work.get("description", ""),
        author=course_name,
        occurred_at=coursework_due_at(work),
        url=work.get("alternateLink"),
        raw={
            "course_id": work.get("courseId"),
            "max_points": work.get("maxPoints"),
            "work_type": work.get("workType"),
            "state": work.get("state"),
        },
    )


class ClassroomConnector:
    provider = PROVIDER

    def __init__(self, token_store) -> None:  # noqa: ANN001
        self.tokens = token_store

    async def _get(self, account_id: uuid.UUID, path: str, **params) -> dict:
        token = await self.tokens.access_token(account_id)
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(
                f"{API}{path}", headers={"Authorization": f"Bearer {token}"}, params=params or None
            )
        if response.status_code == 401:
            raise Forbidden("reauth_required: Classroom rejected the access token")
        if response.status_code == 403:
            # The common case, and worth its own message: the account is real and the
            # token is valid, but the school has not authorized this app.
            raise Forbidden(
                "classroom_not_authorized: the domain administrator must allow this app"
            )
        response.raise_for_status()
        return response.json()

    async def sync(self, account_id: uuid.UUID, cursor: str | None) -> SyncPage:
        """Active courses → their coursework, published items only.

        A draft assignment is not a commitment; surfacing one as a deadline would train
        the user to distrust the deadline list.
        """
        courses = await self._get(account_id, "/courses", courseStates="ACTIVE", pageSize=50)
        items: list[SyncItem] = []
        for course in courses.get("courses", []):
            work = await self._get(
                account_id,
                f"/courses/{course['id']}/courseWork",
                courseWorkStates="PUBLISHED",
                pageSize=100,
            )
            items.extend(
                normalize_coursework(w, course_name=course.get("name"))
                for w in work.get("courseWork", [])
            )
        return SyncPage(items=items, cursor=None, has_more=False)

    async def fetch(self, account_id: uuid.UUID, object_id: str) -> SyncItem | None:
        course_id, _, work_id = object_id.partition(":")
        work = await self._get(account_id, f"/courses/{course_id}/courseWork/{work_id}")
        return normalize_coursework(work) if work else None

    async def execute(self, account_id: uuid.UUID, action: str, args: dict) -> ProviderEvidence:
        raise ValueError(f"classroom connector is read-only; cannot perform {action}")

    async def revoke(self, account_id: uuid.UUID) -> None:
        account = await self.tokens.get_account(account_id)
        account.revoked_at = datetime.now(UTC)
        account.credentials = {}
