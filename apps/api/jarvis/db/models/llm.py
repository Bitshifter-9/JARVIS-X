"""LLM call accounting and provider health.

Two tables that exist because the router needs memory:

* ``llm_calls`` — every call, with provider, model, tokens and a cost estimate, so spend
  is a query rather than a surprise at the end of the month.
* ``provider_health`` — consecutive failures and a cooldown, so the cascade stops
  hammering a provider that is rate-limiting or down.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from jarvis.db.base import Base, Timestamps, UUIDPrimaryKey


class LLMCall(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "llm_calls"
    __table_args__ = (Index("ix_llm_calls_created_provider", "created_at", "provider"),)

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    # classify | plan | extract | chat
    call_class: Mapped[str] = mapped_column(String(16), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_version: Mapped[str | None] = mapped_column(String(32))

    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_inr: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    status: Mapped[str] = mapped_column(String(16), nullable=False)  # ok | error | skipped
    error: Mapped[str | None] = mapped_column(Text)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    correlation_id: Mapped[str | None] = mapped_column(String(32), index=True)


class ProviderHealth(Timestamps, Base):
    """One row per provider. The provider name *is* the key — there is only ever one."""

    __tablename__ = "provider_health"

    provider: Mapped[str] = mapped_column(String(32), primary_key=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cooldown_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    total_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class ExtractionCache(Base):
    """Exact-payload cache for extraction.

    Providers redeliver the same message, and a redelivery must not cost a second call to
    a rate-limited free tier. Keyed on the prompt version too, so editing a prompt
    invalidates every entry rather than silently serving old answers.
    """

    __tablename__ = "extraction_cache"

    cache_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    prompt_version: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
