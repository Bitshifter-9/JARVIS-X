"""``python -m macnode`` — pair this Mac, or run the helper.

    python -m macnode pair  --api https://jarvis.example.com --email you@example.com
    python -m macnode run   --api https://jarvis.example.com --email you@example.com
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import sys
from pathlib import Path

import httpx
from jarvis.services.device.keys import generate_keypair, sign

from macnode import keychain
from macnode.node import MacNode, NodeConfig

CONFIG_PATH = Path.home() / ".jarvis-x" / "node.json"
DEFAULT_BUNDLES = [
    "com.google.Chrome",
    "com.microsoft.VSCode",
    "com.apple.Safari",
]


def _login(api: str, email: str) -> str:
    password = getpass.getpass(f"Password for {email}: ")
    response = httpx.post(
        f"{api}/v1/auth/login", json={"email": email, "password": password}, timeout=15
    )
    response.raise_for_status()
    return response.json()["access_token"]


def pair(args: argparse.Namespace) -> int:
    token = _login(args.api, args.email)
    headers = {"Authorization": f"Bearer {token}"}

    private_pem = keychain.load_private_key()
    if private_pem is None:
        private_pem, public_pem = generate_keypair()
        location = keychain.store_private_key(private_pem)
        print(f"Generated a device key. Private half stored in: {location}")
    else:
        from cryptography.hazmat.primitives import serialization

        key = serialization.load_pem_private_key(private_pem.encode(), password=None)
        public_pem = key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode()
        print("Reusing the existing device key.")

    started = httpx.post(
        f"{args.api}/v1/devices/pair",
        headers=headers,
        json={
            "name": args.name,
            "public_key_pem": public_pem,
            "allowed_bundle_ids": args.bundles or DEFAULT_BUNDLES,
            "capabilities": ["mac.open_app", "mac.focus_app"],
        },
        timeout=15,
    )
    started.raise_for_status()
    challenge = started.json()

    completed = httpx.post(
        f"{args.api}/v1/devices/pair/complete",
        headers=headers,
        json={
            "challenge": challenge["challenge"],
            "signature": sign(private_pem, challenge["challenge"].encode()),
        },
        timeout=15,
    )
    completed.raise_for_status()
    device = completed.json()

    server_key = httpx.get(
        f"{args.api}/v1/devices/server-key", headers=headers, timeout=15
    ).json()["public_key_pem"]

    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        json.dumps(
            {
                "api": args.api,
                "email": args.email,
                "device_id": device["id"],
                "server_public_pem": server_key,
                "allowed_bundle_ids": device["allowed_bundle_ids"],
            },
            indent=2,
        )
    )
    print(f"Paired as {device['name']} ({device['fingerprint']}…)")
    print(f"Config written to {CONFIG_PATH}")
    return 0


def run(args: argparse.Namespace) -> int:
    if not CONFIG_PATH.exists():
        print("Not paired yet. Run: python -m macnode pair --api ... --email ...", file=sys.stderr)
        return 1

    config = json.loads(CONFIG_PATH.read_text())
    private_pem = keychain.load_private_key()
    if private_pem is None:
        print("Device key is missing. Re-pair this Mac.", file=sys.stderr)
        return 1

    api = args.api or config["api"]
    token = _login(api, args.email or config["email"])

    node = MacNode(
        NodeConfig(
            api_ws_url=api.replace("https://", "wss://").replace("http://", "ws://")
            + "/v1/devices/ws",
            access_token=token,
            device_id=config["device_id"],
            device_private_pem=private_pem,
            server_public_pem=config["server_public_pem"],
            allowed_bundle_ids=set(config["allowed_bundle_ids"]),
        )
    )
    print(f"Connecting as device {config['device_id']}… Ctrl-C to stop.")
    try:
        asyncio.run(node.run_forever())
    except KeyboardInterrupt:
        node.stop()
        print("\nStopped.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="macnode", description="JARVIS X Mac helper")
    sub = parser.add_subparsers(dest="command", required=True)

    pair_cmd = sub.add_parser("pair", help="pair this Mac with your account")
    pair_cmd.add_argument("--api", required=True)
    pair_cmd.add_argument("--email", required=True)
    pair_cmd.add_argument("--name", default="My Mac")
    pair_cmd.add_argument("--bundles", nargs="*", help="bundle ids this Mac may open")
    pair_cmd.set_defaults(func=pair)

    run_cmd = sub.add_parser("run", help="run the helper")
    run_cmd.add_argument("--api")
    run_cmd.add_argument("--email")
    run_cmd.set_defaults(func=run)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
