"""FastAPI dependencies: database session and the authenticated caller."""

from __future__ import annotations

import uuid
from typing import Annotated

import jwt
from fastapi import Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis.core.errors import Forbidden, Unauthorized
from jarvis.core.security import decode_access_token
from jarvis.db.models.identity import User
from jarvis.db.session import get_session
from jarvis.services.identity import IdentityService

SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def get_identity_service(session: SessionDep) -> IdentityService:
    return IdentityService(session)


IdentityDep = Annotated[IdentityService, Depends(get_identity_service)]


async def current_user(
    session: SessionDep,
    authorization: Annotated[str | None, Header()] = None,
) -> User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise Unauthorized("Missing bearer token")

    try:
        claims = decode_access_token(authorization[7:])
    except jwt.PyJWTError as exc:
        raise Unauthorized("Invalid or expired token") from exc

    try:
        user_id = uuid.UUID(claims["sub"])
    except (KeyError, ValueError) as exc:
        raise Unauthorized("Token subject is not a user id") from exc

    user = await session.get(User, user_id)
    if user is None:
        raise Unauthorized("Account no longer exists")
    if not user.is_active:
        raise Forbidden("Account is disabled")
    return user


CurrentUser = Annotated[User, Depends(current_user)]
