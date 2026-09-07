"""YouTube upload — the one effectful call in the video pipeline.

Resumable upload in two requests: POST the metadata to get a session URL, PUT the
bytes. Reuses the Google OAuth account (provider "gmail") with the ``youtube.upload``
scope, which the user grants via ``/v1/connectors/google/authorize?include_youtube=true``.

Uploads from an unaudited Google API project are forced private by YouTube regardless
of the requested status — request the audit in the Cloud console before expecting
public uploads.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

import httpx
from jarvis.core.errors import Forbidden
from jarvis.core.logging import get_logger

log = get_logger(__name__)

UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
DATA_API = "https://www.googleapis.com/youtube/v3"
ANALYTICS_API = "https://youtubeanalytics.googleapis.com/v2/reports"
SCOPE = "https://www.googleapis.com/auth/youtube.upload"


class YouTubeConnector:
    provider = "youtube"

    def __init__(self, token_store) -> None:  # noqa: ANN001
        self.tokens = token_store

    async def upload(
        self,
        account_id: uuid.UUID,
        *,
        file_path: str,
        title: str,
        description: str = "",
        tags: list[str] | None = None,
        privacy: str = "private",
        **_: object,
    ) -> str:
        """Upload one video; returns the YouTube video id."""
        account = await self.tokens.get_account(account_id)
        if SCOPE not in (account.scopes or []):
            raise Forbidden(
                "reauth_required: this Google account was connected without youtube.upload; "
                "reconnect via /v1/connectors/google/authorize?include_youtube=true"
            )
        token = await self.tokens.access_token(account_id)

        body = {
            "snippet": {
                "title": title[:100],
                "description": description[:4900],
                "tags": (tags or [])[:30],
                "categoryId": "28",  # Science & Technology
            },
            "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False},
        }

        async with httpx.AsyncClient(timeout=httpx.Timeout(600, connect=30)) as client:
            start = await client.post(
                f"{UPLOAD_URL}?uploadType=resumable&part=snippet,status",
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-Upload-Content-Type": "video/mp4",
                },
                json=body,
            )
            if start.status_code == 401:
                raise Forbidden("reauth_required: YouTube rejected the access token")
            if start.status_code >= 400:
                # Google's body names the real cause: accessNotConfigured means the
                # YouTube Data API v3 is not enabled on the Cloud project;
                # youtubeSignupRequired means the account has no YouTube channel.
                raise Forbidden(
                    f"YouTube refused the upload ({start.status_code}): "
                    f"{start.text[:300]}"
                )

            # ponytail: single-shot PUT of the whole file from memory — a Short is
            # tens of MB; switch to chunked uploads with resume offsets if long-form
            # videos ever go through here.
            content = await asyncio.to_thread(Path(file_path).read_bytes)
            put = await client.put(
                start.headers["location"],
                content=content,
                headers={"Content-Type": "video/mp4"},
            )
            put.raise_for_status()

        video_id = put.json()["id"]
        log.info("youtube_uploaded", video_id=video_id, privacy=privacy)
        return video_id

    async def _get(self, account_id: uuid.UUID, url: str, **params) -> dict:
        token = await self.tokens.access_token(account_id)
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(
                url, headers={"Authorization": f"Bearer {token}"}, params=params
            )
        if response.status_code == 401:
            raise Forbidden("reauth_required: YouTube rejected the access token")
        if response.status_code == 403:
            raise Forbidden(
                "reauth_required: missing scope — reconnect Google with "
                "scripts/connect_google.py --youtube to grant analytics/comment access"
            )
        response.raise_for_status()
        return response.json()

    async def channel_report(self, account_id: uuid.UUID, *, days: int = 28) -> dict:
        """Channel totals and the top videos over the window, titles included."""
        from datetime import UTC, datetime, timedelta

        end = datetime.now(UTC).date()
        window = {"startDate": (end - timedelta(days=days)).isoformat(),
                  "endDate": end.isoformat(), "ids": "channel==MINE"}
        metrics = "views,estimatedMinutesWatched,averageViewDuration"

        totals = await self._get(account_id, ANALYTICS_API, metrics=metrics, **window)
        per_video = await self._get(
            account_id, ANALYTICS_API,
            metrics=metrics, dimensions="video", sort="-views", maxResults=10, **window,
        )

        rows = per_video.get("rows") or []
        titles: dict[str, str] = {}
        if rows:
            listing = await self._get(
                account_id, f"{DATA_API}/videos",
                id=",".join(r[0] for r in rows), part="snippet",
            )
            titles = {
                item["id"]: item["snippet"]["title"] for item in listing.get("items", [])
            }

        total_row = (totals.get("rows") or [[0, 0, 0]])[0]
        return {
            "window_days": days,
            "totals": {
                "views": total_row[0],
                "watch_minutes": total_row[1],
                "avg_view_seconds": total_row[2],
            },
            "videos": [
                {
                    "video_id": r[0],
                    "title": titles.get(r[0], r[0]),
                    "views": r[1],
                    "watch_minutes": r[2],
                    "avg_view_seconds": r[3],
                }
                for r in rows
            ],
            "fetched_at": datetime.now(UTC).isoformat(),
        }

    async def list_comments(self, account_id: uuid.UUID, video_id: str) -> list[dict]:
        """Top-level comment threads on one video, newest first."""
        data = await self._get(
            account_id, f"{DATA_API}/commentThreads",
            part="snippet", videoId=video_id, maxResults=20,
            order="time", textFormat="plainText",
        )
        threads = []
        for item in data.get("items", []):
            top = item["snippet"]["topLevelComment"]
            threads.append({
                "parent_id": top["id"],
                "video_id": video_id,
                "author": top["snippet"].get("authorDisplayName", ""),
                "text": top["snippet"].get("textDisplay", ""),
                "reply_count": item["snippet"].get("totalReplyCount", 0),
            })
        return threads

    async def reply_comment(
        self, account_id: uuid.UUID, *, parent_id: str, text: str, **_: object
    ) -> str:
        """Post one reply; returns the created comment id."""
        token = await self.tokens.access_token(account_id)
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"{DATA_API}/comments",
                params={"part": "snippet"},
                headers={"Authorization": f"Bearer {token}"},
                json={"snippet": {"parentId": parent_id, "textOriginal": text[:1000]}},
            )
        if response.status_code in (401, 403):
            raise Forbidden(f"reauth_required: YouTube refused the reply ({response.status_code})")
        response.raise_for_status()
        reply_id = response.json()["id"]
        log.info("youtube_comment_replied", parent_id=parent_id, reply_id=reply_id)
        return reply_id
