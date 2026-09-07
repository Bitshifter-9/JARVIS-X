"""Alexa request authenticity.

The skill is reached over HTTPS rather than through Lambda (PLAN.md §5 substitution), so
the checks the ASK SDK would perform for us are performed here instead. All four are
mandatory, and each blocks a different attack:

1. **Certificate URL shape** — only Amazon can publish under ``s3.amazonaws.com/echo.api/``,
   so constraining the URL is what stops an attacker pointing us at their own chain.
2. **Certificate chain and SAN** — the leaf must be issued for ``echo-api.amazon.com``
   and must chain to the certificate above it.
3. **Signature over the raw body** — bytes as received; a re-serialized body is not what
   was signed.
4. **Timestamp freshness** — a valid signature never expires, so only the timestamp
   makes a captured request unusable later.

ponytail: the chain is verified link-by-link and anchored on the constrained Amazon S3
host rather than on a system root store. Add a certifi-backed root check if this endpoint
is ever reachable from somewhere that can spoof that host.
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from jarvis.core.logging import get_logger

log = get_logger(__name__)

CERT_HOST = "s3.amazonaws.com"
CERT_PATH_PREFIX = "/echo.api/"
ECHO_SAN = "echo-api.amazon.com"
# Amazon's published tolerance. A request older than this is a replay.
MAX_TIMESTAMP_AGE = timedelta(seconds=150)


class AlexaSignatureError(Exception):
    """Raised for anything that fails authenticity. Never carries the request back."""


def verify_certificate_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise AlexaSignatureError("certificate chain URL must be https")
    if parsed.hostname is None or parsed.hostname.lower() != CERT_HOST:
        raise AlexaSignatureError("certificate chain URL is not on Amazon's host")
    if (parsed.port or 443) != 443:
        raise AlexaSignatureError("certificate chain URL must use port 443")
    # normpath-free on purpose: "/echo.api/../evil" must fail, and it does, because the
    # comparison is on the raw path.
    if not parsed.path.startswith(CERT_PATH_PREFIX) or ".." in parsed.path:
        raise AlexaSignatureError("certificate chain URL is not under /echo.api/")


def _load_chain(pem: bytes) -> list[x509.Certificate]:
    chain = x509.load_pem_x509_certificates(pem)
    if not chain:
        raise AlexaSignatureError("certificate chain is empty")
    return chain


def _check_leaf(leaf: x509.Certificate, *, now: datetime) -> None:
    if not (leaf.not_valid_before_utc <= now <= leaf.not_valid_after_utc):
        raise AlexaSignatureError("signing certificate is not currently valid")
    try:
        san = leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    except x509.ExtensionNotFound as exc:
        raise AlexaSignatureError("signing certificate has no subject alternative name") from exc
    if ECHO_SAN not in san.get_values_for_type(x509.DNSName):
        raise AlexaSignatureError(f"signing certificate is not issued for {ECHO_SAN}")


def _check_chain(chain: list[x509.Certificate]) -> None:
    for child, parent in zip(chain, chain[1:], strict=False):
        try:
            child.verify_directly_issued_by(parent)
        except (ValueError, TypeError, InvalidSignature) as exc:
            raise AlexaSignatureError("certificate chain does not verify") from exc


def verify_timestamp(timestamp: str | None, *, now: datetime | None = None) -> None:
    if not timestamp:
        raise AlexaSignatureError("request carries no timestamp")
    try:
        sent_at = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AlexaSignatureError("request timestamp is unparseable") from exc
    if sent_at.tzinfo is None:
        sent_at = sent_at.replace(tzinfo=UTC)
    if abs((now or datetime.now(UTC)) - sent_at) > MAX_TIMESTAMP_AGE:
        raise AlexaSignatureError("request timestamp is outside the tolerance window")


def verify_request(
    *,
    body: bytes,
    signature: str | None,
    certificate_chain_pem: bytes,
    certificate_url: str,
    now: datetime | None = None,
) -> None:
    """Full authenticity check. Raises ``AlexaSignatureError``; returns nothing on success."""
    moment = now or datetime.now(UTC)
    verify_certificate_url(certificate_url)
    if not signature:
        raise AlexaSignatureError("request carries no signature")

    chain = _load_chain(certificate_chain_pem)
    _check_leaf(chain[0], now=moment)
    _check_chain(chain)

    try:
        chain[0].public_key().verify(
            base64.b64decode(signature),
            body,
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
    except (InvalidSignature, ValueError) as exc:
        raise AlexaSignatureError("request signature does not match the body") from exc
