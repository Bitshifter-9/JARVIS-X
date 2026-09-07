"""First-party authentication for the Flutter apps."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Header, status
from pydantic import BaseModel, EmailStr, Field

from jarvis.api.deps import CurrentUser, IdentityDep, SessionDep
from jarvis.core.config import get_settings
from jarvis.core.errors import Forbidden, Unauthorized

router = APIRouter(prefix="/v1/auth", tags=["auth"])

# ── Sign in with Google ────────────────────────────────────────────────
# The browser can't hand tokens back to a desktop app, so the app starts the flow,
# the user finishes it in the browser (reusing the connector callback that is already
# registered with Google), and the app polls until the tokens are parked. Parked in
# Postgres: a deploy or restart in the middle of the flow used to drop the attempt.
GOOGLE_LOGIN_PREFIX = "glogin_"
_LOGIN_TTL = timedelta(minutes=5)


async def _prune_logins(session) -> None:  # noqa: ANN001
    from sqlalchemy import delete

    from jarvis.db.models.identity import PendingLogin

    await session.execute(
        delete(PendingLogin).where(PendingLogin.created_at < datetime.now(UTC) - _LOGIN_TTL)
    )


async def is_google_login_state(session, state: str) -> bool:  # noqa: ANN001
    from jarvis.db.models.identity import PendingLogin

    if not state.startswith(GOOGLE_LOGIN_PREFIX):
        return False
    return (await session.get(PendingLogin, state)) is not None


async def _userinfo(access_token: str) -> dict[str, Any]:
    import httpx

    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.get(
            "https://openidconnect.googleapis.com/v1/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
        )
    response.raise_for_status()
    return response.json()


async def complete_google_login(state: str, access_token: str, identity) -> None:  # noqa: ANN001
    """Called by the connector callback when the state marks a login, not a connect.

    Resolves the Google identity to a Jarvis account (creating one on first sign-in)
    and parks a session for the app's next poll.
    """
    from jarvis.db.models.identity import PendingLogin

    session = identity.session
    entry = await session.get(PendingLogin, state)
    if entry is None or entry.created_at < datetime.now(UTC) - _LOGIN_TTL:
        raise Forbidden("This sign-in attempt expired — try again from the app")

    info = await _userinfo(access_token)
    email = info.get("email")
    if not email:
        raise Forbidden("Google returned no email address")

    user = await identity.find_by_email(email)
    if user is None:
        # First sign-in creates the account; the random password is never used —
        # Google is the credential. It can be reset later if password login is wanted.
        user = await identity.register(
            email, secrets.token_urlsafe(24), display_name=info.get("name")
        )
    access, refresh, expires_in = await identity.issue_session(user, user_agent="google-signin")
    entry.tokens = {"access_token": access, "refresh_token": refresh, "expires_in": expires_in}
    await session.flush()


class RegisterRequest(BaseModel):
    email: EmailStr
    # 12 characters, because this account can approve actions on your real devices.
    password: str = Field(min_length=12, max_length=256)
    display_name: str | None = Field(default=None, max_length=200)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "Bearer"  # noqa: S105 — the RFC 6749 literal, not a credential
    expires_in: int


class UserResponse(BaseModel):
    id: str
    email: str
    display_name: str | None
    timezone: str


@router.get("/google/start")
async def google_login_start(session: SessionDep) -> dict[str, str]:
    """Begin Sign in with Google. The app opens the URL and then polls."""
    from urllib.parse import urlencode

    from jarvis.db.models.identity import PendingLogin

    await _prune_logins(session)
    state = GOOGLE_LOGIN_PREFIX + secrets.token_urlsafe(24)
    session.add(PendingLogin(state=state, tokens=None))
    await session.flush()
    s = get_settings()
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(
        {
            "client_id": s.google_client_id,
            # Reuses the connector callback already registered with Google; the state
            # prefix is what routes it to login handling instead of account linking.
            "redirect_uri": f"{s.base_url}/v1/connectors/google/callback",
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
        }
    )
    return {"authorization_url": url, "poll_token": state}


class GooglePollRequest(BaseModel):
    poll_token: str = Field(max_length=64)


@router.post("/google/poll")
async def google_login_poll(body: GooglePollRequest, session: SessionDep) -> dict[str, Any]:
    """The app calls this every couple of seconds until the browser flow finishes."""
    from jarvis.db.models.identity import PendingLogin

    entry = await session.get(PendingLogin, body.poll_token)
    if entry is None or entry.created_at < datetime.now(UTC) - _LOGIN_TTL:
        raise Unauthorized("Sign-in expired or unknown — start again")
    if entry.tokens is None:
        return {"pending": True}
    tokens = dict(entry.tokens)
    # Consumed: a second poll (or a replay) gets nothing.
    await session.delete(entry)
    await session.flush()
    return {"pending": False, **tokens, "token_type": "Bearer"}


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(body: RegisterRequest, identity: IdentityDep) -> UserResponse:
    user = await identity.register(body.email, body.password, display_name=body.display_name)
    return UserResponse(
        id=str(user.id),
        email=user.email,
        display_name=user.display_name,
        timezone=user.timezone,
    )


@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    identity: IdentityDep,
    user_agent: Annotated[str | None, Header()] = None,
) -> TokenResponse:
    user = await identity.authenticate(body.email, body.password)
    access, refresh, expires_in = await identity.issue_session(user, user_agent=user_agent)
    return TokenResponse(access_token=access, refresh_token=refresh, expires_in=expires_in)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(body: RefreshRequest, identity: IdentityDep) -> TokenResponse:
    access, new_refresh, expires_in = await identity.refresh_session(body.refresh_token)
    return TokenResponse(access_token=access, refresh_token=new_refresh, expires_in=expires_in)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(body: RefreshRequest, identity: IdentityDep) -> None:
    if not await identity.revoke_refresh_token(body.refresh_token):
        raise Unauthorized("Token is unknown or already revoked")


@router.post("/sessions/revoke-all")
async def revoke_all(user: CurrentUser, identity: IdentityDep) -> dict[str, int]:
    """One of the kill switch's three levers: drop every session for this account."""
    return {"revoked": await identity.revoke_all_sessions(user.id)}


@router.get("/me", response_model=UserResponse)
async def me(user: CurrentUser) -> UserResponse:
    return UserResponse(
        id=str(user.id),
        email=user.email,
        display_name=user.display_name,
        timezone=user.timezone,
    )
