"""First-party authentication for the Flutter apps."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Header, status
from pydantic import BaseModel, EmailStr, Field

from jarvis.api.deps import CurrentUser, IdentityDep
from jarvis.core.errors import Unauthorized

router = APIRouter(prefix="/v1/auth", tags=["auth"])


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


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(body: RegisterRequest, identity: IdentityDep) -> UserResponse:
    user = await identity.register(
        body.email, body.password, display_name=body.display_name
    )
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
