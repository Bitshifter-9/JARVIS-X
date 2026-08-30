"""Users, sessions, and the OAuth2 authorization-server tables.

We host our own OAuth2 authorization-code + PKCE grant because Alexa account linking
requires a real one (blueprint §16). Running it ourselves keeps a single identity system
rather than bolting a second one alongside JWT auth.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from jarvis.db.base import Base, Timestamps, UUIDPrimaryKey


class User(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(200))
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="Asia/Kolkata")
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    @property
    def is_active(self) -> bool:
        return self.disabled_at is None


class Identity(UUIDPrimaryKey, Timestamps, Base):
    """A non-password way of being recognised: a Telegram chat id, an Alexa subject, a device.

    Every surface is untrusted until its identity maps to a user (blueprint §2). These rows
    are that map, and they are deliberately separate from ``users`` so revoking one channel
    never disturbs another.
    """

    __tablename__ = "identities"
    __table_args__ = (
        Index("uq_identities_provider_subject", "provider", "subject", unique=True),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)  # telegram | alexa | device
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    is_owner: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RefreshToken(UUIDPrimaryKey, Timestamps, Base):
    """Opaque refresh tokens, stored hashed.

    A database read alone must not be enough to mint a session, which is why only the
    SHA-256 of the token is persisted.
    """

    __tablename__ = "refresh_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    client_id: Mapped[str | None] = mapped_column(String(128))
    user_agent: Mapped[str | None] = mapped_column(String(300))


class OAuthClient(UUIDPrimaryKey, Timestamps, Base):
    """A registered relying party — the Alexa skill, the Flutter apps, the Mac node."""

    __tablename__ = "oauth_clients"

    client_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    client_secret_hash: Mapped[str | None] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    redirect_uris: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    scopes: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    # Public clients (mobile, desktop) cannot hold a secret, so PKCE is mandatory for them.
    is_public: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OAuthCode(UUIDPrimaryKey, Timestamps, Base):
    """A single-use authorization code.

    ``consumed_at`` is set inside the same transaction that issues tokens, so a replayed
    code finds the row already spent.
    """

    __tablename__ = "oauth_codes"

    code_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    client_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    redirect_uri: Mapped[str] = mapped_column(Text, nullable=False)
    scope: Mapped[str] = mapped_column(Text, nullable=False, default="")
    code_challenge: Mapped[str | None] = mapped_column(String(128))
    code_challenge_method: Mapped[str | None] = mapped_column(String(8))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
