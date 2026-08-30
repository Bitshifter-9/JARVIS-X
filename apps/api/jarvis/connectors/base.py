"""The connector contract (blueprint §17).

Every provider implements the same six operations, so the agent never learns which
service a task came from. Outbound scopes are separate from read scopes and are requested
only when the user enables the feature that needs them.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol


@dataclass(frozen=True)
class SyncItem:
    provider: str
    object_id: str
    kind: str
    title: str | None = None
    body: str | None = None
    author: str | None = None
    occurred_at: datetime | None = None
    url: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SyncPage:
    items: list[SyncItem]
    cursor: str | None = None
    has_more: bool = False


@dataclass(frozen=True)
class ProviderEvidence:
    """What a provider says it created. ``object_id`` is the only proof that counts."""

    object_id: str | None
    url: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


class Connector(Protocol):
    provider: str

    async def sync(self, account_id: uuid.UUID, cursor: str | None) -> SyncPage: ...
    async def fetch(self, account_id: uuid.UUID, object_id: str) -> SyncItem | None: ...
    async def execute(self, account_id: uuid.UUID, action: str, args: dict) -> ProviderEvidence: ...
    async def revoke(self, account_id: uuid.UUID) -> None: ...
