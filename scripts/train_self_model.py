"""Personal LoRA (#20): fine-tune a small on-device adapter on *your* corpus, so the local
model's voice and facts become yours — cheap, incremental, offline.

This is the "improve our model" step made literal. It runs on the Mac (Apple Silicon) with
mlx-lm, pulls your own writing and learned facts from your account, builds a training set, and
trains a LoRA adapter you can load into the local model.

    uv run python -m scripts.train_self_model --api http://localhost:8000 --email you@example.com

Nothing leaves the Mac except the API read of your own data; the model and adapter stay local.
Requires: pip install mlx-lm  (and a base model, default a 4-bit 3B instruct model).
"""

from __future__ import annotations

import argparse
import getpass
import json
import subprocess
import sys
from pathlib import Path

import httpx

DEFAULT_MODEL = "mlx-community/Qwen2.5-3B-Instruct-4bit"
OUT = Path.home() / ".jarvis" / "self-lora"


def _login(api: str, email: str) -> str:
    password = getpass.getpass(f"Password for {email}: ")
    r = httpx.post(f"{api}/v1/auth/login", json={"email": email, "password": password}, timeout=20)
    r.raise_for_status()
    return r.json()["access_token"]


def _corpus(api: str, token: str) -> list[str]:
    """Your own words + the durable facts JARVIS learned, as training text."""
    headers = {"Authorization": f"Bearer {token}"}
    lines: list[str] = []
    export = httpx.get(f"{api}/v1/export", headers=headers, timeout=60).json()
    for m in export.get("messages", []):
        if m.get("role") == "user" and (m.get("content") or "").strip():
            lines.append(m["content"].strip())
    for mem in export.get("memories", []):
        content = (mem.get("content") or "").strip()
        if content and mem.get("kind") in ("semantic", "source"):
            lines.append(content)
    model = httpx.get(f"{api}/v1/self-model", headers=headers, timeout=60).json().get("model", {})
    persona = model.get("persona", {})
    for key in ("about", "priorities", "people", "learned_style"):
        if (persona.get(key) or "").strip():
            lines.append(persona[key].strip())
    seen: set[str] = set()
    out = []
    for line in lines:
        if len(line) >= 12 and line not in seen:
            seen.add(line)
            out.append(line)
    return out


def _write_dataset(lines: list[str], data_dir: Path) -> int:
    data_dir.mkdir(parents=True, exist_ok=True)
    split = max(1, len(lines) // 10)
    with (data_dir / "train.jsonl").open("w") as f:
        for line in lines[split:]:
            f.write(json.dumps({"text": line}) + "\n")
    with (data_dir / "valid.jsonl").open("w") as f:
        for line in lines[:split]:
            f.write(json.dumps({"text": line}) + "\n")
    return len(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fine-tune a personal LoRA on your own corpus (#20)"
    )
    parser.add_argument("--api", required=True)
    parser.add_argument("--email", required=True)
    parser.add_argument("--model", default=DEFAULT_MODEL, help="base model (mlx-lm compatible)")
    parser.add_argument("--iters", type=int, default=300)
    args = parser.parse_args()

    try:
        import mlx_lm  # noqa: F401
    except ImportError:
        print("mlx-lm is not installed. On your Apple-Silicon Mac:\n    pip install mlx-lm",
              file=sys.stderr)
        return 1

    token = _login(args.api, args.email)
    lines = _corpus(args.api, token)
    if len(lines) < 20:
        print(f"Only {len(lines)} training lines — chat and use JARVIS more first, then retrain.")
        return 1

    data_dir = OUT / "data"
    n = _write_dataset(lines, data_dir)
    print(f"Built {n} training lines → {data_dir}")

    OUT.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable, "-m", "mlx_lm.lora", "--model", args.model, "--train",
        "--data", str(data_dir), "--iters", str(args.iters),
        "--adapter-path", str(OUT / "adapter"),
    ]
    print("Training:", " ".join(cmd))
    code = subprocess.call(cmd)  # noqa: S603 — your own local mlx-lm run, args are yours
    if code == 0:
        print(f"\nDone. Your adapter is at {OUT / 'adapter'}. Load it with:\n"
              f"    mlx_lm.generate --model {args.model} --adapter-path {OUT / 'adapter'} "
              f'--prompt "…"')
    return code


if __name__ == "__main__":
    raise SystemExit(main())
