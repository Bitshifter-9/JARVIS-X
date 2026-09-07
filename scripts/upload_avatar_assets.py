"""Upload the assets the video pipeline clones from: a voice sample and a hero video.

The voice sample is ~15 seconds of clean speech (.wav/.mp3/.m4a). The hero video is a
short, silent clip of you facing the camera (.mp4/.mov). Once they exist, the pipeline
speaks in your voice, and lip-syncs the hero video when JARVIS_YOUTUBE_LIPSYNC_CMD is
configured.

    uv run python -m scripts.upload_avatar_assets --voice my_voice.wav --avatar hero.mp4
"""

from __future__ import annotations

import argparse
import mimetypes
import sys
from pathlib import Path

import httpx

DEMO_EMAIL = "demo@jarvis-x.dev"
DEMO_PASSWORD = "demo-password-12345"  # noqa: S105


def fail(message: str) -> None:
    print(f"\n✗ {message}\n", file=sys.stderr)
    sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", default="http://localhost:8000")
    parser.add_argument("--email", default=DEMO_EMAIL)
    parser.add_argument("--password", default=DEMO_PASSWORD)
    parser.add_argument("--voice", type=Path, help="voice sample (.wav/.mp3/.m4a)")
    parser.add_argument("--avatar", type=Path, help="silent hero video (.mp4/.mov)")
    parser.add_argument("--music", type=Path, help="royalty-free BGM (.mp3/.wav/.m4a)")
    args = parser.parse_args()

    uploads = [
        (k, p)
        for k, p in (("voice", args.voice), ("avatar", args.avatar), ("music", args.music))
        if p
    ]
    if not uploads:
        fail("nothing to upload — pass --voice and/or --avatar")
    for _, path in uploads:
        if not path.exists():
            fail(f"{path} does not exist")

    with httpx.Client(base_url=args.api, timeout=300) as client:
        login = client.post(
            "/v1/auth/login", json={"email": args.email, "password": args.password}
        )
        if login.status_code >= 400:
            fail(f"login failed ({login.status_code}): {login.text[:200]}")
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        print(f"✓ signed in as {args.email}")

        for kind, path in uploads:
            mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            with path.open("rb") as f:
                response = client.post(
                    "/v1/youtube/assets",
                    data={"kind": kind},
                    files={"file": (path.name, f, mime)},
                    headers=headers,
                )
            if response.status_code >= 400:
                fail(f"{kind} upload failed ({response.status_code}): {response.text[:300]}")
            body = response.json()
            print(f"✓ {kind}: {body['filename']} ({body['bytes'] / 2**20:.1f} MB)")

        status = client.get("/v1/youtube/assets", headers=headers).json()
        print(f"\nvoice clone ready: {status['voice_clone_ready']}"
              f"  (needs `uv sync --extra avatar` if false with a voice uploaded)")
        print(f"lip-sync configured: {status['lipsync_configured']}"
              f"  (set JARVIS_YOUTUBE_LIPSYNC_CMD to enable avatar video)")


if __name__ == "__main__":
    main()
