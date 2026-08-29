# legacy/ — JARVIS v1, preserved for reference

Source of [`Bitshifter-9/Jarvis-`](https://github.com/Bitshifter-9/Jarvis-) at commit `11c24ce`,
copied here on 2026-08-30.

**Nothing in the new codebase imports from this directory.** It is a reference for porting, and a record of
what v1 did well.

## What was excluded

To keep the repo small (253 MB → 104 KB), these were dropped and must be re-downloaded if you want to run v1:

- `voices/*.onnx` — Piper voice models (~60 MB each). Re-fetch from
  [rhasspy/piper-voices](https://huggingface.co/rhasspy/piper-voices).
- `input.wav`, `output.wav` — scratch audio.
- `Jarvis.app/`, `Jarvis.icns`, `icon.png` — packaged bundle and icons.
- `__pycache__/`, `.vscode/`.

## Porting verdicts

Full reasoning in [`../PLAN.md` §11](../PLAN.md).

| File | Verdict |
|---|---|
| `tools.py` → `open_app` | Concept kept, implementation replaced. `os.system("open -a ...")` has no verification. Becomes typed `mac.open_app(bundle_id)` on `NSWorkspace` with an allowlist and frontmost-window evidence. |
| `tools.py` → `run_command` | **Deleted.** `subprocess.check_output(cmd, shell=True)` driven by model output is arbitrary chat-to-shell — the exact R4 the policy engine exists to prevent. Replaced by predefined command templates. |
| `voice_jarvis.py` record/transcribe | **Ported** into `apps/mac-node`. faster-whisper int8 works well; adds a visible listening indicator and push-to-talk. |
| `voice_jarvis.py` → `speak()` | **Ported.** Piper TTS with sentence-buffered streaming. |
| `memory.py` (ChromaDB) | **Replaced** by `pgvector`. Same embedding model; memory now joins goals and tasks in one query under one tenant filter. |
| `knowledge/rag.py` | **Reworked.** Whole-file embedding loses precision — chunk it and store provenance so retrieval can cite its source. |
| `brain.py` | **Superseded.** A free-running `while True` with no budget, policy or verification is precisely the loop the state machine replaces. |
| `app.py` | **Patterns ported.** Token streaming and the WebSocket envelope were good; they become the Mac node transport. |
| `static/` | **Kept as design reference** — the HUD visual language feeds the Flutter theme. |
| `try_tools()` | **Superseded.** `if "chrome" in text` also fires on "close chrome". Replaced by typed proposals through the policy engine. |

The two deletions are the point: `run_command` and the unbounded loop were the real vulnerabilities.
Removing them is a talking point, not an omission.
