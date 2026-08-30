"""Device and server signing keys.

Two independent key pairs, each proving a different thing (blueprint §12):

* The **device key** (ECDSA P-256, private half generated inside the Mac's Keychain and
  never exported) proves *this Mac is the one that paired*.
* The **server key** proves *this job really came from JARVIS*, so a compromised network
  path cannot inject work into a helper that holds real macOS permissions.

P-256 rather than Ed25519 because macOS Keychain generates and stores P-256 natively via
``SecKeyCreateRandomKey``; a key the Keychain cannot hold would have to live in a file,
which is the property we are specifically trying to avoid.
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

CURVE = ec.SECP256R1()


def generate_keypair() -> tuple[str, str]:
    """Return ``(private_pem, public_pem)``. Used by the helper and in tests."""
    private = ec.generate_private_key(CURVE)
    private_pem = private.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_pem = private.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return private_pem, public_pem


def fingerprint(public_pem: str) -> str:
    """A stable short name for a key: SHA-256 over its DER encoding.

    Over the DER rather than the PEM text, so whitespace or line-ending differences
    between platforms cannot produce two fingerprints for one key.
    """
    public = serialization.load_pem_public_key(public_pem.encode())
    der = public.public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return hashlib.sha256(der).hexdigest()


def sign(private_pem: str, message: bytes) -> str:
    private = serialization.load_pem_private_key(private_pem.encode(), password=None)
    signature = private.sign(message, ec.ECDSA(hashes.SHA256()))
    return base64.b64encode(signature).decode()


def verify(public_pem: str, message: bytes, signature_b64: str) -> bool:
    """Verify a signature. Returns False rather than raising.

    Every caller of this is a security decision, and a boolean at the call site is
    harder to accidentally swallow in a broad ``except`` than an exception is.
    """
    try:
        public = serialization.load_pem_public_key(public_pem.encode())
        public.verify(
            base64.b64decode(signature_b64), message, ec.ECDSA(hashes.SHA256())
        )
    except (InvalidSignature, ValueError, TypeError):
        return False
    return True
