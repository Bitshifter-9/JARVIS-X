"""Canvas LMS, read-only (PLAN.md phase 5.3).

The demoable LMS. Classroom needs a school administrator to authorize the app (§13);
a free Canvas teacher instance needs an access token and nothing else, so this is the
connector that actually runs in the demo. Both normalize to the same ``SyncItem``, so
the goal engine cannot tell which one a deadline came from.

Canvas is *self-hosted per institution*, so the host is data, not a constant — and
because it is data, it is validated before it is dialled.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from urllib.parse import urlparse

import httpx
from jarvis.connectors.base import ProviderEvidence, SyncItem, SyncPage
from jarvis.core.errors import Forbidden
from jarvis.core.logging import get_logger

log = get_logger(__name__)
PROVIDER = "canvas"


def _checked_base_url(base_url: str) -> str:
    """An institution host we are willing to dial.

    A connector base URL is user-supplied configuration, which makes it a request-forgery
    vector: ``http://169.254.169.254`` would turn a "sync my assignments" tick into a
    metadata-service read. HTTPS and a public hostname, or nothing.
    """
    parsed = urlparse(base_url.rstrip("/"))
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("Canvas base URL must be an https:// host")
    if parsed.hostname in ("localhost", "metadata") or parsed.hostname.startswith(
        ("127.", "10.", "192.168.", "169.254.")
    ):
        raise ValueError("Canvas base URL must not point inside the host network")
    return f"{parsed.scheme}://{parsed.netloc}"


def normalize_assignment(assignment: dict, *, course_name: str | None = None) -> SyncItem:
    due = assignment.get("due_at")
    due_at = None
    if due:
        due_at = datetime.fromisoformat(str(due).replace("Z", "+00:00"))
        if due_at.tzinfo is None:
            due_at = due_at.replace(tzinfo=UTC)

    return SyncItem(
        provider=PROVIDER,
        object_id=f"{assignment.get('course_id', '')}:{assignment['id']}",
        kind="assignment",
        title=assignment.get("name", "(untitled assignment)"),
        body=assignment.get("description", "") or "",
        author=course_name,
        occurred_at=due_at,
        url=assignment.get("html_url"),
        raw={
            "course_id": assignment.get("course_id"),
            "points_possible": assignment.get("points_possible"),
            # Canvas reports this per requesting user; a submitted assignment is done.
            "has_submitted_submissions": assignment.get("has_submitted_submissions"),
            "locked": assignment.get("locked_for_user"),
        },
    )


class CanvasConnector:
    provider = PROVIDER

    def __init__(self, base_url: str, api_token: str, *, timeout: float = 20.0) -> None:
        self.base_url = _checked_base_url(base_url)
        self.api_token = api_token
        self.timeout = timeout

    async def _get(self, path: str, **params) -> list | dict:
        if not self.api_token:
            raise RuntimeError("JARVIS_CANVAS_API_TOKEN is not configured")
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(
                f"{self.base_url}/api/v1{path}",
                headers={"Authorization": f"Bearer {self.api_token}"},
                params=params or None,
            )
        if response.status_code in (401, 403):
            raise Forbidden("reauth_required: Canvas rejected the access token")
        response.raise_for_status()
        return response.json()

    async def sync(self, account_id: uuid.UUID, cursor: str | None) -> SyncPage:
        """Active courses → their upcoming assignments.

        ``bucket=upcoming`` is asked of Canvas rather than filtered here: a term's worth
        of past assignments is not a commitment, and not fetching them is cheaper than
        fetching and discarding them.
        """
        courses = await self._get("/courses", enrollment_state="active", per_page=50)
        items: list[SyncItem] = []
        for course in courses if isinstance(courses, list) else []:
            assignments = await self._get(
                f"/courses/{course['id']}/assignments", bucket="upcoming", per_page=100
            )
            items.extend(
                normalize_assignment(a, course_name=course.get("name"))
                for a in (assignments if isinstance(assignments, list) else [])
            )
        return SyncPage(items=items, cursor=None, has_more=False)

    async def fetch(self, account_id: uuid.UUID, object_id: str) -> SyncItem | None:
        course_id, _, assignment_id = object_id.partition(":")
        data = await self._get(f"/courses/{course_id}/assignments/{assignment_id}")
        return normalize_assignment(data) if isinstance(data, dict) else None

    async def execute(self, account_id: uuid.UUID, action: str, args: dict) -> ProviderEvidence:
        raise ValueError(f"canvas connector is read-only; cannot perform {action}")

    async def revoke(self, account_id: uuid.UUID) -> None:
        self.api_token = ""
