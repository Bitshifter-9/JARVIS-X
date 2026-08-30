"""Connector authorization and the privacy surface.

Blueprint §25: the connector screen shows scopes, last access, stored data and retention,
with Disconnect **and** Delete. Connecting an account is easy; the point of this module is
that disconnecting is equally easy and actually removes what was stored.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse
from sqlalchemy import delete, func, select

from jarvis.api.deps import CurrentUser, SessionDep
from jarvis.connectors.google.oauth import (
    READ_SCOPES,
    WRITE_SCOPES,
    TokenStore,
    authorization_url,
    exchange_code,
)
from jarvis.core.config import get_settings
from jarvis.core.errors import Forbidden, NotFound
from jarvis.core.logging import get_logger
from jarvis.db.models.source import ConnectorCursor, SourceAccount, SourceObject

log = get_logger(__name__)
router = APIRouter(prefix="/v1/connectors", tags=["connectors"])

STATE_TTL = timedelta(minutes=10)


def _sign_state(user_id: uuid.UUID) -> str:
    """The OAuth ``state`` parameter, signed.

    The callback arrives from Google with no session, so ``state`` is the only thing
    carrying the user's identity. Signing it is what stops someone attaching *their*
    Google account to *your* JARVIS account by replaying a crafted callback.
    """
    s = get_settings()
    return jwt.encode(
        {
            "sub": str(user_id),
            "purpose": "connector_oauth",
            "exp": datetime.now(UTC) + STATE_TTL,
        },
        s.jwt_secret,
        algorithm=s.jwt_algorithm,
    )


def _verify_state(state: str) -> uuid.UUID:
    s = get_settings()
    try:
        claims = jwt.decode(state, s.jwt_secret, algorithms=[s.jwt_algorithm])
    except jwt.PyJWTError as exc:
        raise Forbidden("Invalid or expired authorization state") from exc
    if claims.get("purpose") != "connector_oauth":
        raise Forbidden("State was issued for something else")
    return uuid.UUID(claims["sub"])


@router.get("")
async def list_connectors(user: CurrentUser, session: SessionDep) -> list[dict[str, Any]]:
    """What is connected, what it can see, and how much it has stored."""
    accounts = list(
        (
            await session.scalars(
                select(SourceAccount).where(SourceAccount.user_id == user.id)
            )
        ).all()
    )

    result = []
    for account in accounts:
        stored = await session.scalar(
            select(func.count())
            .select_from(SourceObject)
            .where(SourceObject.account_id == account.id)
        )
        result.append(
            {
                "id": str(account.id),
                "provider": account.provider,
                "display_name": account.display_name,
                "scopes": account.scopes,
                "status": account.status,
                "last_synced_at": (
                    account.last_synced_at.isoformat() if account.last_synced_at else None
                ),
                "connected_at": account.created_at.isoformat(),
                "revoked_at": account.revoked_at.isoformat() if account.revoked_at else None,
                "stored_objects": stored or 0,
            }
        )
    return result


@router.get("/google/authorize")
async def google_authorize(
    user: CurrentUser,
    include_write: bool = Query(
        default=False,
        description="Also request send/create scopes. Off by default: outbound permission "
        "is requested only when a feature that needs it is enabled.",
    ),
) -> dict[str, Any]:
    """Begin the Google flow. Returns the URL to open in a browser."""
    return {
        "authorization_url": authorization_url(
            _sign_state(user.id), include_write=include_write
        ),
        "scopes": READ_SCOPES + (WRITE_SCOPES if include_write else []),
    }


@router.get("/google/callback", response_class=HTMLResponse, response_model=None)
async def google_callback(
    session: SessionDep,
    state: str,
    code: str | None = None,
    error: str | None = None,
):
    """Where Google sends the user back.

    Unauthenticated by necessity — the browser arrives here from Google, not from the app —
    so ``state`` is the only proof of who started the flow.
    """
    if error:
        return HTMLResponse(_page("Authorization cancelled", f"Google reported: {error}"), 400)
    if not code:
        return HTMLResponse(_page("Authorization failed", "No code was returned."), 400)

    user_id = _verify_state(state)
    tokens = await exchange_code(code)

    store = TokenStore(session)
    account = await store.find(user_id, "gmail")
    if account is None:
        account = SourceAccount(
            user_id=user_id,
            provider="gmail",
            external_id=tokens.email or f"google:{user_id}",
            display_name=tokens.email or "Google account",
            scopes=tokens.scopes,
        )
        session.add(account)
        await session.flush()

    await store.store(account, tokens)
    log.info("connector_linked", provider="gmail", scopes=len(tokens.scopes))

    granted = "\n".join(f"<li>{s.rsplit('/', 1)[-1]}</li>" for s in tokens.scopes)
    return HTMLResponse(
        _page(
            "Google connected",
            f"JARVIS X can now read the following:<ul>{granted}</ul>"
            "You can disconnect at any time from the Connectors screen.",
        )
    )


@router.post("/{account_id}/disconnect")
async def disconnect(
    account_id: uuid.UUID,
    user: CurrentUser,
    session: SessionDep,
    delete_data: bool = Query(
        default=True, description="Also delete everything fetched through this connector."
    ),
) -> dict[str, Any]:
    """Disconnect, and by default delete what was stored.

    Deleting is the default rather than an extra step: a connector that keeps your mail
    after you disconnect it has not really been disconnected.
    """
    account = await session.scalar(
        select(SourceAccount).where(
            SourceAccount.id == account_id, SourceAccount.user_id == user.id
        )
    )
    if account is None:
        raise NotFound("Connected account")

    account.revoked_at = datetime.now(UTC)
    account.status = "revoked"
    account.credentials = {}

    deleted = 0
    if delete_data:
        result = await session.execute(
            delete(SourceObject).where(SourceObject.account_id == account_id).returning(
                SourceObject.id
            )
        )
        deleted = len(result.fetchall())
        await session.execute(
            delete(ConnectorCursor).where(ConnectorCursor.account_id == account_id)
        )

    await session.flush()
    log.warning(
        "connector_disconnected", provider=account.provider, objects_deleted=deleted
    )
    return {
        "disconnected": True,
        "provider": account.provider,
        "objects_deleted": deleted,
        "credentials_cleared": True,
    }


def _page(title: str, body: str) -> str:
    from html import escape

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(title)} — JARVIS X</title>
<style>
 body{{font:16px/1.6 system-ui,sans-serif;background:#0f1621;color:#e6edf3;
      display:grid;place-items:center;min-height:100vh;margin:0;padding:1rem}}
 .card{{background:#161f2c;padding:2rem;border-radius:12px;max-width:30rem}}
 h1{{font-size:1.25rem;margin:0 0 .75rem}} ul{{color:#8b98a9;font-size:.9rem}}
</style></head>
<body><div class="card"><h1>{escape(title)}</h1><div>{body}</div></div></body></html>"""
