"""Password hashing, JWTs, and the primitives the OAuth2 grant needs.

Argon2id is the password hash: memory-hard, so a leaked table resists GPU cracking in a
way bcrypt no longer reliably does.

Tokens are short-lived JWTs. Refresh tokens are opaque random strings stored *hashed*, so
a database read alone cannot mint a session — the same reasoning as passwords.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from jarvis.core.config import get_settings

_hasher = PasswordHasher()


# ── passwords ──────────────────────────────────────────────────────────
def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    try:
        return _hasher.verify(hashed, password)
    except (VerifyMismatchError, InvalidHashError, ValueError):
        return False


def needs_rehash(hashed: str) -> bool:
    """True when the stored hash used weaker parameters than we now require."""
    try:
        return _hasher.check_needs_rehash(hashed)
    except (InvalidHashError, ValueError):
        return True


# ── opaque tokens (refresh tokens, device tokens, pairing challenges) ──
def new_opaque_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def hash_token(token: str) -> str:
    """SHA-256 for high-entropy tokens.

    Argon2 is for *low*-entropy secrets a human chose. A 256-bit random token needs no
    key stretching, and stretching it on every request would only cost latency.
    """
    return hashlib.sha256(token.encode()).hexdigest()


def tokens_equal(a: str, b: str) -> bool:
    return hmac.compare_digest(a, b)


# ── JWT access tokens ──────────────────────────────────────────────────
def create_access_token(
    subject: str, *, scopes: list[str] | None = None, extra: dict[str, Any] | None = None
) -> str:
    s = get_settings()
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": subject,
        "iss": s.oauth_issuer,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=s.access_token_ttl_minutes)).timestamp()),
        "jti": secrets.token_urlsafe(16),
        "scope": " ".join(scopes or []),
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, s.jwt_secret, algorithm=s.jwt_algorithm)


def decode_access_token(token: str) -> dict[str, Any]:
    """Decode and verify. Raises ``jwt.PyJWTError`` on any failure.

    ``algorithms`` is pinned to the configured algorithm — accepting the token's own
    ``alg`` header is the classic JWT confusion bug.
    """
    s = get_settings()
    return jwt.decode(
        token,
        s.jwt_secret,
        algorithms=[s.jwt_algorithm],
        issuer=s.oauth_issuer,
        options={"require": ["exp", "iat", "sub", "iss"]},
    )


# ── PKCE (RFC 7636) ────────────────────────────────────────────────────
def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def pkce_challenge(verifier: str, method: str = "S256") -> str:
    if method == "S256":
        return _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    if method == "plain":
        return verifier
    raise ValueError(f"unsupported code_challenge_method: {method}")


def verify_pkce(verifier: str, challenge: str, method: str = "S256") -> bool:
    try:
        return hmac.compare_digest(pkce_challenge(verifier, method), challenge)
    except (ValueError, UnicodeEncodeError):
        return False


# ── approval payload binding (blueprint §9) ────────────────────────────
def canonical_json(value: Any) -> str:
    """Deterministic JSON: sorted keys, no incidental whitespace.

    Two structurally identical payloads must hash identically, or an approval could be
    replayed against a re-serialized copy of itself.
    """
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def approval_payload_hash(
    *, tool: str, args: dict[str, Any], user_id: str, device_id: str | None, expires_at: datetime
) -> str:
    """SHA-256 over tool + args + user + device + expiry.

    Editing any component yields a different hash, which is the mechanism by which an
    edited proposal becomes a *new* proposal requiring a fresh approval.
    """
    payload = canonical_json(
        {
            "tool": tool,
            "args": args,
            "user_id": user_id,
            "device_id": device_id,
            "expires_at": expires_at.astimezone(UTC).isoformat(),
        }
    )
    return "sha256:" + hashlib.sha256(payload.encode()).hexdigest()
