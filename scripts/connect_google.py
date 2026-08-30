"""Connect a Google account, with errors that say what to do.

Replaces a curl pipeline that failed with a JSON decode traceback whenever anything was
off — which told you nothing about which thing.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
import webbrowser

DEMO_EMAIL = "demo@jarvis-x.dev"
DEMO_PASSWORD = "demo-password-12345"  # noqa: S105


def _checked(url: str) -> str:
    """Only http(s). ``--api`` is user-supplied, and urllib would happily open file://."""
    if not url.startswith(("http://", "https://")):
        raise ValueError(f"refusing non-http URL: {url!r}")
    return url


def fail(message: str, fix: str | None = None) -> None:
    print(f"\n✗ {message}", file=sys.stderr)
    if fix:
        print(f"\n  {fix}\n", file=sys.stderr)
    sys.exit(1)


def request(url: str, *, data: dict | None = None, token: str | None = None) -> dict:
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(  # noqa: S310 — _checked rejects non-http schemes
        _checked(url), data=body, method="POST" if body else "GET"
    )
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=15) as response:  # noqa: S310
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode()[:400]
        try:
            problem = json.loads(detail)
            detail = problem.get("detail") or problem.get("title") or detail
        except json.JSONDecodeError:
            pass
        fail(f"{url} returned {exc.code}: {detail}")
    except urllib.error.URLError as exc:
        fail(
            f"cannot reach the API at {url} ({exc.reason})",
            "Start it in another terminal:  make api",
        )
    return {}


def main() -> int:
    parser = argparse.ArgumentParser(description="Connect Google to JARVIS X")
    parser.add_argument("--api", default="http://localhost:8000")
    parser.add_argument("--email", default=DEMO_EMAIL)
    parser.add_argument("--password", default=DEMO_PASSWORD)
    parser.add_argument("--write", action="store_true", help="also request send scopes")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    api = args.api.rstrip("/")

    health = request(f"{api}/healthz")
    print(f"✓ API reachable (build {health.get('build')}, env {health.get('env')})")

    tokens = request(
        f"{api}/v1/auth/login", data={"email": args.email, "password": args.password}
    )
    print(f"✓ signed in as {args.email}")

    query = "?include_write=true" if args.write else ""
    result = request(
        f"{api}/v1/connectors/google/authorize{query}", token=tokens["access_token"]
    )
    url = result["authorization_url"]

    if "client_id=&" in url or "client_id=" not in url:
        fail(
            "the authorization URL has no client_id",
            "JARVIS_GOOGLE_CLIENT_ID is unset in .env — note that .env.example is a\n"
            "  template and is never read. Then restart: make api",
        )

    print(f"✓ requesting {len(result['scopes'])} scope(s):")
    for scope in result["scopes"]:
        print(f"    · {scope.rsplit('/', 1)[-1]}")

    print("\nOpen this and sign in:\n")
    print(f"  {url}\n")
    if not args.no_browser:
        webbrowser.open(url)
        print("(opened in your browser)")

    print(
        "\nAfter accepting you will land on a 'Google connected' page. Then check:\n"
        "  make connectors\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
