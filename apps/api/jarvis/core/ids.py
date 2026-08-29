"""Identifiers.

Two families, deliberately:

* **UUIDv7 primary keys** — time-ordered, so B-tree inserts stay at the right edge of the
  index instead of scattering. Postgres stores them as native ``uuid``.
* **Prefixed ULIDs for wire ids** — ``evt_...``, ``cor_...``, ``job_...``. Human-sortable,
  self-describing in a log line, and impossible to confuse across types.

Both encode time, so either can be sorted chronologically without consulting a column.
"""

from __future__ import annotations

import os
import time
import uuid

# Crockford base32: no I, L, O or U, so it survives being read aloud or transcribed.
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def uuid7() -> uuid.UUID:
    """A UUIDv7: 48-bit millisecond timestamp, then randomness (RFC 9562)."""
    ms = int(time.time() * 1000) & 0xFFFF_FFFF_FFFF
    rand = os.urandom(10)
    b = bytearray(ms.to_bytes(6, "big") + rand)
    b[6] = (b[6] & 0x0F) | 0x70  # version 7
    b[8] = (b[8] & 0x3F) | 0x80  # RFC 4122 variant
    return uuid.UUID(bytes=bytes(b))


def _ulid() -> str:
    """26-character Crockford base32 ULID: 48-bit time + 80-bit randomness."""
    value = (int(time.time() * 1000) << 80) | int.from_bytes(os.urandom(10), "big")
    return "".join(_CROCKFORD[(value >> shift) & 0x1F] for shift in range(125, -1, -5))


def new_id(prefix: str) -> str:
    """A prefixed wire id, e.g. ``new_id("evt")`` -> ``evt_01J9Z...``."""
    return f"{prefix}_{_ulid()}"


def new_event_id() -> str:
    return new_id("evt")


def new_correlation_id() -> str:
    return new_id("cor")


def new_job_id() -> str:
    return new_id("job")
