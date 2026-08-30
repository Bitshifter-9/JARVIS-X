"""Show what is connected, what it can read, and how much it has stored."""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

from scripts.connect_google import DEMO_EMAIL, DEMO_PASSWORD, _checked, request


def main() -> int:
    parser = argparse.ArgumentParser(description="List connected accounts")
    parser.add_argument("--api", default="http://localhost:8000")
    parser.add_argument("--email", default=DEMO_EMAIL)
    parser.add_argument("--password", default=DEMO_PASSWORD)
    args = parser.parse_args()

    api = args.api.rstrip("/")
    tokens = request(
        f"{api}/v1/auth/login", data={"email": args.email, "password": args.password}
    )

    # noqa on the constructor: _checked rejects anything but http(s).
    req = urllib.request.Request(_checked(f"{api}/v1/connectors"))  # noqa: S310
    req.add_header("Authorization", f"Bearer {tokens['access_token']}")
    try:
        with urllib.request.urlopen(req, timeout=15) as response:  # noqa: S310
            accounts = json.loads(response.read())
    except urllib.error.URLError as exc:
        print(f"cannot reach the API: {exc}", file=sys.stderr)
        return 1

    if not accounts:
        print("Nothing connected yet.  Run:  make connect-google")
        return 0

    for a in accounts:
        state = "revoked" if a["revoked_at"] else a["status"]
        print(f"\n{a['provider']}  ({state})")
        print(f"  id            {a['id']}")
        print(f"  account       {a['display_name'] or '—'}")
        print(f"  connected     {a['connected_at']}")
        print(f"  last synced   {a['last_synced_at'] or 'never'}")
        print(f"  stored items  {a['stored_objects']}")
        print("  scopes")
        for s in a["scopes"]:
            print(f"    · {s.rsplit('/', 1)[-1]}")
    print(
        "\nDisconnect and delete stored data:\n"
        "  curl -X POST localhost:8000/v1/connectors/<id>/disconnect \\\n"
        "    -H \"Authorization: Bearer <token>\"\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
