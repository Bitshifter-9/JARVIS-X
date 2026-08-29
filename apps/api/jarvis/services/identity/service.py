"""Identity service.

Two ways in, one user table:

* **First-party sessions** — email + password, issuing a short JWT and a rotating opaque
  refresh token. Used by the Flutter apps.
* **OAuth2 authorization-code + PKCE** — used by Alexa account linking, which requires a
  real authorization server (blueprint §16). Hosting it ourselves keeps one identity
  system instead of two.

The grant is implemented directly rather than through a framework, so every check is
visible and individually tested: exact redirect-uri match, single-use codes, mandatory
PKCE for public clients, and refresh-token rotation.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from jarvis.core.config import get_settings
from jarvis.core.errors import Conflict, Forbidden, Unauthorized
from jarvis.core.logging import get_logger
from jarvis.core.security import (
    create_access_token,
    hash_password,
    hash_token,
    needs_rehash,
    new_opaque_token,
    verify_password,
    verify_pkce,
)
from jarvis.db.models.identity import OAuthClient, OAuthCode, RefreshToken, User
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

log = get_logger(__name__)


class OAuthError(Exception):
    """An OAuth2 protocol error, carrying the code the RFC requires in the response."""

    def __init__(self, error: str, description: str, *, status: int = 400) -> None:
        self.error = error
        self.description = description
        self.status = status
        super().__init__(f"{error}: {description}")


class IdentityService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ── users ──────────────────────────────────────────────────────────
    async def register(
        self, email: str, password: str, *, display_name: str | None = None
    ) -> User:
        email = email.strip().lower()
        if await self.session.scalar(select(User).where(User.email == email)):
            raise Conflict("An account with that email already exists")

        user = User(
            email=email,
            password_hash=hash_password(password),
            display_name=display_name,
            timezone=get_settings().timezone,
        )
        self.session.add(user)
        await self.session.flush()
        log.info("user_registered", user_id=str(user.id))
        return user

    async def authenticate(self, email: str, password: str) -> User:
        """Verify credentials.

        The same error is returned whether the account is missing or the password is
        wrong, so the endpoint cannot be used to enumerate registered addresses.
        """
        user = await self.session.scalar(select(User).where(User.email == email.strip().lower()))
        if user is None or not verify_password(password, user.password_hash):
            raise Unauthorized("Invalid email or password")
        if not user.is_active:
            raise Forbidden("Account is disabled")

        # Transparently upgrade a hash written under weaker parameters.
        if needs_rehash(user.password_hash):
            user.password_hash = hash_password(password)
            await self.session.flush()
        return user

    async def get_user(self, user_id: uuid.UUID) -> User | None:
        return await self.session.get(User, user_id)

    # ── first-party sessions ───────────────────────────────────────────
    async def issue_session(
        self,
        user: User,
        *,
        scopes: list[str] | None = None,
        client_id: str | None = None,
        user_agent: str | None = None,
    ) -> tuple[str, str, int]:
        """Return ``(access_token, refresh_token, expires_in)``.

        The refresh token is returned once, in this response, and stored only as a hash.
        """
        s = get_settings()
        access = create_access_token(str(user.id), scopes=scopes)
        refresh = new_opaque_token()

        self.session.add(
            RefreshToken(
                user_id=user.id,
                token_hash=hash_token(refresh),
                expires_at=datetime.now(UTC) + timedelta(days=s.refresh_token_ttl_days),
                client_id=client_id,
                user_agent=(user_agent or "")[:300] or None,
            )
        )
        await self.session.flush()
        return access, refresh, s.access_token_ttl_minutes * 60

    async def refresh_session(self, refresh_token: str) -> tuple[str, str, int]:
        """Rotate a refresh token.

        The presented token is revoked as part of issuing its replacement, so a stolen
        token stops working the moment the legitimate client next refreshes.
        """
        record = await self.session.scalar(
            select(RefreshToken).where(RefreshToken.token_hash == hash_token(refresh_token))
        )
        if record is None or record.revoked_at is not None:
            raise Unauthorized("Invalid refresh token")
        if record.expires_at <= datetime.now(UTC):
            raise Unauthorized("Refresh token expired")

        user = await self.session.get(User, record.user_id)
        if user is None or not user.is_active:
            raise Unauthorized("Account unavailable")

        record.revoked_at = datetime.now(UTC)
        await self.session.flush()
        return await self.issue_session(user, client_id=record.client_id)

    async def revoke_refresh_token(self, refresh_token: str) -> bool:
        record = await self.session.scalar(
            select(RefreshToken).where(RefreshToken.token_hash == hash_token(refresh_token))
        )
        if record is None or record.revoked_at is not None:
            return False
        record.revoked_at = datetime.now(UTC)
        await self.session.flush()
        return True

    async def revoke_all_sessions(self, user_id: uuid.UUID) -> int:
        """Used by the kill switch's 'revoke device sessions' lever."""
        rows = (
            await self.session.scalars(
                select(RefreshToken).where(
                    RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None)
                )
            )
        ).all()
        now = datetime.now(UTC)
        for row in rows:
            row.revoked_at = now
        await self.session.flush()
        log.warning("all_sessions_revoked", user_id=str(user_id), count=len(rows))
        return len(rows)

    # ── OAuth2 authorization-code + PKCE ───────────────────────────────
    async def get_client(self, client_id: str) -> OAuthClient:
        client = await self.session.scalar(
            select(OAuthClient).where(OAuthClient.client_id == client_id)
        )
        if client is None or client.disabled_at is not None:
            raise OAuthError("invalid_client", "Unknown or disabled client")
        return client

    async def validate_authorization_request(
        self,
        *,
        client_id: str,
        redirect_uri: str,
        response_type: str,
        code_challenge: str | None,
        code_challenge_method: str | None,
    ) -> OAuthClient:
        client = await self.get_client(client_id)

        # Exact match, not prefix: a prefix match lets an open redirect on the client's
        # domain become a token exfiltration path.
        if redirect_uri not in client.redirect_uris:
            raise OAuthError("invalid_request", "redirect_uri is not registered for this client")
        if response_type != "code":
            raise OAuthError("unsupported_response_type", "Only the code grant is supported")
        if client.is_public and not code_challenge:
            raise OAuthError("invalid_request", "PKCE is required for public clients")
        if code_challenge and code_challenge_method not in ("S256", "plain"):
            raise OAuthError("invalid_request", "code_challenge_method must be S256 or plain")
        if code_challenge_method == "plain" and client.is_public:
            raise OAuthError("invalid_request", "Public clients must use S256")
        return client

    async def issue_authorization_code(
        self,
        *,
        user: User,
        client: OAuthClient,
        redirect_uri: str,
        scope: str,
        code_challenge: str | None,
        code_challenge_method: str | None,
    ) -> str:
        s = get_settings()
        code = new_opaque_token()
        self.session.add(
            OAuthCode(
                code_hash=hash_token(code),
                client_id=client.client_id,
                user_id=user.id,
                redirect_uri=redirect_uri,
                scope=scope,
                code_challenge=code_challenge,
                code_challenge_method=code_challenge_method,
                expires_at=datetime.now(UTC) + timedelta(seconds=s.oauth_code_ttl_seconds),
            )
        )
        await self.session.flush()
        log.info("oauth_code_issued", client_id=client.client_id, user_id=str(user.id))
        return code

    async def exchange_code(
        self,
        *,
        code: str,
        client_id: str,
        redirect_uri: str,
        code_verifier: str | None,
        client_secret: str | None,
    ) -> tuple[str, str, int, str]:
        """Exchange an authorization code for tokens.

        Every failure path below is a real attack that has worked against real
        implementations, which is why each has its own test.
        """
        client = await self.get_client(client_id)

        if not client.is_public:
            if not client_secret or client.client_secret_hash is None:
                raise OAuthError("invalid_client", "Client authentication required", status=401)
            if hash_token(client_secret) != client.client_secret_hash:
                raise OAuthError("invalid_client", "Bad client credentials", status=401)

        record = await self.session.scalar(
            select(OAuthCode).where(OAuthCode.code_hash == hash_token(code))
        )
        if record is None:
            raise OAuthError("invalid_grant", "Unknown authorization code")
        if record.consumed_at is not None:
            # Replay. The code is already spent; treat it as hostile and say nothing more.
            log.warning("oauth_code_replay", client_id=client_id)
            raise OAuthError("invalid_grant", "Authorization code already used")
        if record.expires_at <= datetime.now(UTC):
            raise OAuthError("invalid_grant", "Authorization code expired")
        if record.client_id != client_id:
            raise OAuthError("invalid_grant", "Code was issued to a different client")
        if record.redirect_uri != redirect_uri:
            raise OAuthError("invalid_grant", "redirect_uri does not match the authorization")

        if record.code_challenge:
            if not code_verifier:
                raise OAuthError("invalid_grant", "code_verifier is required")
            if not verify_pkce(
                code_verifier, record.code_challenge, record.code_challenge_method or "S256"
            ):
                raise OAuthError("invalid_grant", "PKCE verification failed")

        # Consume inside the same transaction that issues tokens, so a concurrent
        # replay finds the row already spent.
        record.consumed_at = datetime.now(UTC)
        await self.session.flush()

        user = await self.session.get(User, record.user_id)
        if user is None or not user.is_active:
            raise OAuthError("invalid_grant", "Account unavailable")

        scopes = record.scope.split() if record.scope else []
        access, refresh, expires_in = await self.issue_session(
            user, scopes=scopes, client_id=client_id
        )
        return access, refresh, expires_in, record.scope

    async def register_client(
        self,
        *,
        client_id: str,
        name: str,
        redirect_uris: list[str],
        scopes: list[str] | None = None,
        is_public: bool = False,
        client_secret: str | None = None,
    ) -> OAuthClient:
        client = OAuthClient(
            client_id=client_id,
            name=name,
            redirect_uris=redirect_uris,
            scopes=scopes or [],
            is_public=is_public,
            client_secret_hash=hash_token(client_secret) if client_secret else None,
        )
        self.session.add(client)
        await self.session.flush()
        return client
