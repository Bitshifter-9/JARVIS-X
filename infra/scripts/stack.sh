#!/usr/bin/env bash
# The three processes, one terminal, one Ctrl-C. Logs interleave with a prefix.
set -euo pipefail
trap 'kill 0' EXIT INT TERM
prefix() { sed -u "s/^/[$1] /"; }
JARVIS_BUILD_SHA=$(git rev-parse --short HEAD 2>/dev/null || echo dev) \
  uv run uvicorn jarvis.main:app --host 0.0.0.0 --port 8000 2>&1 | prefix api &
uv run python -m jarvis.workers.main 2>&1 | prefix worker &
uv run python -m jarvis.workers.scheduler 2>&1 | prefix scheduler &
wait
