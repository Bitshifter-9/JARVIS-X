"""Google OAuth for Gmail and Calendar.

Read scopes only for now. `gmail.send` and `calendar.events` are requested separately,
when the user turns on a feature that needs them.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from jarvis.core.config import get_settings
from jarvis.core.errors import Forbidden, NotFound
from jarvis.db.models.source import SourceAccount
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

READ_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
]
WRITE_SCOPES = [
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/calendar.events",
]
YOUTUBE_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    # Replying to comments needs force-ssl; the analytics loop needs the report scope.
    "https://www.googleapis.com/auth/youtube.force-ssl",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]
# Requested only when the user connects Classroom: a school account that never
# authorizes this app would otherwise fail the whole consent screen.
CLASSROOM_SCOPES = [
    "https://www.googleapis.com/auth/classroom.courses.readonly",
    "https://www.googleapis.com/auth/classroom.coursework.me.readonly",
]
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105 — an endpoint, not a secret


@dataclass(frozen=True)
class GoogleTokens:
    access_token: str
    refresh_token: str | None
    expires_at: datetime
    scopes: list[str]
    email: str | None = None


def authorization_url(
    state: str,
    *,
    include_write: bool = False,
    include_youtube: bool = False,
    include_classroom: bool = False,
) -> str:
    from urllib.parse import urlencode

    s = get_settings()
    scopes = (
        READ_SCOPES
        + (WRITE_SCOPES if include_write else [])
        + (YOUTUBE_SCOPES if include_youtube else [])
        + (CLASSROOM_SCOPES if include_classroom else [])
    )
    return f"{AUTH_URL}?" + urlencode(
        {
            "client_id": s.google_client_id,
            "redirect_uri": f"{s.base_url}/v1/connectors/google/callback",
            "response_type": "code",
            "scope": " ".join(scopes),
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
        }
    )


async def exchange_code(code: str) -> GoogleTokens:
    import httpx

    s = get_settings()
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            TOKEN_URL,
            data={
                "code": code,
                "client_id": s.google_client_id,
                "client_secret": s.google_client_secret,
                "redirect_uri": f"{s.base_url}/v1/connectors/google/callback",
                "grant_type": "authorization_code",
            },
        )
    if response.status_code >= 400:
        raise Forbidden(f"Google rejected the authorization code: {response.text[:200]}")

    data = response.json()
    return GoogleTokens(
        access_token=data["access_token"],
        refresh_token=data.get("refresh_token"),
        expires_at=datetime.now(UTC) + timedelta(seconds=int(data.get("expires_in", 3600))),
        scopes=data.get("scope", "").split(),
    )


async def refresh_access_token(refresh_token: str) -> GoogleTokens:
    import httpx

    s = get_settings()
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            TOKEN_URL,
            data={
                "refresh_token": refresh_token,
                "client_id": s.google_client_id,
                "client_secret": s.google_client_secret,
                "grant_type": "refresh_token",
            },
        )
    if response.status_code >= 400:
        # invalid_grant means the user revoked us. Never retry a secret.
        raise Forbidden(f"reauth_required: {response.text[:200]}")

    data = response.json()
    return GoogleTokens(
        access_token=data["access_token"],
        refresh_token=refresh_token,
        expires_at=datetime.now(UTC) + timedelta(seconds=int(data.get("expires_in", 3600))),
        scopes=data.get("scope", "").split(),
    )


class TokenStore:
    """Access tokens live in ``source_accounts.raw``; refresh tokens are never returned
    to a client (blueprint §21)."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_account(self, account_id: uuid.UUID) -> SourceAccount:
        account = await self.session.get(SourceAccount, account_id)
        if account is None or account.revoked_at is not None:
            raise NotFound("Connected account")
        return account

    async def access_token(self, account_id: uuid.UUID) -> str:
        account = await self.get_account(account_id)
        stored = account.credentials or {}
        expires_at = stored.get("expires_at")
        if (
            stored.get("access_token")
            and expires_at
            and datetime.fromisoformat(expires_at) > datetime.now(UTC) + timedelta(seconds=60)
        ):
            return stored["access_token"]

        refresh_token = stored.get("refresh_token")
        if not refresh_token:
            raise Forbidden("reauth_required: no refresh token stored; reconnect Google")
        refreshed = await refresh_access_token(refresh_token)
        await self.store(account, refreshed)
        return refreshed.access_token

    async def store(self, account: SourceAccount, tokens: GoogleTokens) -> None:
        payload = {
            "access_token": tokens.access_token,
            # Google omits the refresh token on re-consent; keep the one we have.
            "refresh_token": tokens.refresh_token
            or (account.credentials or {}).get("refresh_token"),
            "expires_at": tokens.expires_at.isoformat(),
        }
        account.scopes = tokens.scopes
        account.status = "active"
        account.credentials = payload
        await self.session.flush()

    async def find(
        self, user_id: uuid.UUID, provider: str, *, external_id: str | None = None
    ) -> SourceAccount | None:
        """The connected account — a specific one by its address, or the first active.

        A user can connect several Google accounts; each is its own row keyed by the
        Gmail address, so a second consent never overwrites the first one's tokens.
        """
        query = select(SourceAccount).where(
            SourceAccount.user_id == user_id,
            SourceAccount.provider == provider,
            SourceAccount.revoked_at.is_(None),
        )
        if external_id:
            query = query.where(SourceAccount.external_id == external_id.lower())
        return await self.session.scalar(query.order_by(SourceAccount.created_at))

    async def list(self, user_id: uuid.UUID, provider: str) -> list[SourceAccount]:
        return list(
            (
                await self.session.scalars(
                    select(SourceAccount)
                    .where(
                        SourceAccount.user_id == user_id,
                        SourceAccount.provider == provider,
                        SourceAccount.revoked_at.is_(None),
                    )
                    .order_by(SourceAccount.created_at)
                )
            ).all()
        )
