"""Key storage on the Mac.

The device private key is generated on this machine and never leaves it. macOS Keychain
is the right home; a file on disk is the fallback for development and for a machine where
Keychain access has not been granted yet.

The file fallback is written 0600 and its path is logged loudly, because "my private key
is in a file" is something the operator should know rather than discover.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

SERVICE = "jarvis-x-device-key"
FALLBACK_PATH = Path.home() / ".jarvis-x" / "device_key.pem"


def _security(*args: str, input_text: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(  # noqa: S603 — fixed argv, no shell
        ["/usr/bin/security", *args],
        input=input_text, capture_output=True, text=True, check=False,
    )


def load_private_key(account: str = "default") -> str | None:
    result = _security("find-generic-password", "-s", SERVICE, "-a", account, "-w")
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip().replace("\\n", "\n")
    if FALLBACK_PATH.exists():
        return FALLBACK_PATH.read_text()
    return None


def store_private_key(private_pem: str, account: str = "default") -> str:
    """Store the key, returning where it went so the caller can say so out loud."""
    result = _security(
        "add-generic-password", "-s", SERVICE, "-a", account,
        "-w", private_pem.replace("\n", "\\n"), "-U",
    )
    if result.returncode == 0:
        return "keychain"

    FALLBACK_PATH.parent.mkdir(parents=True, exist_ok=True)
    FALLBACK_PATH.write_text(private_pem)
    os.chmod(FALLBACK_PATH, 0o600)
    return str(FALLBACK_PATH)


def delete_private_key(account: str = "default") -> None:
    _security("delete-generic-password", "-s", SERVICE, "-a", account)
    FALLBACK_PATH.unlink(missing_ok=True)
