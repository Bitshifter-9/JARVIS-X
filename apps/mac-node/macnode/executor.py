"""Executing admitted jobs, and observing what happened.

The helper reports **observations**, never a verdict. It says the pid it saw and which
bundle id is frontmost; the server's verifier decides whether that satisfies what the
action required. A device must not be able to declare its own success.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote

from jarvis.services.device.protocol import JobEnvelope, JobResult, RejectReason
from jarvis.services.tool_gateway.templates import COMMAND_TEMPLATES

from macnode.adapters import MEDIA_APPS, MEDIA_COMMANDS, MacAdapter
from macnode.guard import GuardVerdict, JobGuard

WHATSAPP = "net.whatsapp.WhatsApp"


SETTINGS = ("wifi", "bluetooth", "dark_mode", "do_not_disturb", "brightness")


class Uploader(Protocol):
    """Hands bytes to the server and returns the artifact id it minted, or ``None``."""

    def __call__(self, path: str, *, kind: str, job_id: str) -> str | None: ...


# How long to wait for an app to actually come forward before reporting what we see.
FRONTMOST_TIMEOUT_SECONDS = 8.0
POLL_INTERVAL_SECONDS = 0.25


@dataclass
class Executor:
    adapter: MacAdapter
    guard: JobGuard
    device_private_pem: str
    # How long to wait for the window server to agree an app is in front. A field rather
    # than a constant so tests can shorten it without patching a method.
    frontmost_timeout: float = FRONTMOST_TIMEOUT_SECONDS
    # Screenshots and files go *up* to the server; the result carries only the id.
    uploader: Uploader | None = None

    def handle(self, envelope: JobEnvelope) -> JobResult:
        verdict: GuardVerdict = self.guard.admit(envelope)
        if not verdict.accepted:
            return self._sign(
                JobResult(
                    job_id=envelope.job_id,
                    status="rejected",
                    reject_reason=verdict.reason.value if verdict.reason else None,
                    error=verdict.detail,
                )
            )

        try:
            observed = self._run(envelope)
        except Exception as exc:  # noqa: BLE001 — a helper crash must become a result
            return self._sign(
                JobResult(
                    job_id=envelope.job_id,
                    status="failed",
                    error=f"{type(exc).__name__}: {exc}",
                )
            )

        return self._sign(JobResult(job_id=envelope.job_id, status="completed", observed=observed))

    def _run(self, envelope: JobEnvelope) -> dict[str, Any]:
        match envelope.action:
            case "mac.open_app":
                return self._open_app(envelope.args["bundle_id"])
            case "mac.focus_app":
                return self._focus_app(envelope.args["bundle_id"])
            case "mac.run_template":
                return self._run_template(envelope.args)
            case "mac.read_ui":
                return self._read_ui(envelope.args["bundle_id"])
            case "mac.press_button":
                return self._press_button(envelope.args["bundle_id"], envelope.args["title"])
            case "mac.capture_window":
                return self._capture(envelope.args["bundle_id"])
            case "mac.file_exists":
                return self._file_exists(envelope.args["path"], envelope.args["scope_bookmark"])
            # ── the hand ───────────────────────────────────────────────
            case "mac.open_url":
                return self._open_url(envelope.args["url"])
            case "mac.open_file":
                return self._open_file(envelope.args["path"], envelope.args["scope_bookmark"])
            case "mac.find_files":
                return self._find_files(envelope.args["query"], envelope.args["scope_bookmark"])
            case "mac.clipboard_read":
                return {"status": 200, "text": self.adapter.clipboard_read()[:4000]}
            case "mac.clipboard_write":
                ok = self.adapter.clipboard_write(str(envelope.args["text"]))
                return {"status": 200 if ok else 500}
            case "mac.notify":
                ok = self.adapter.notify(str(envelope.args["title"]), str(envelope.args["body"]))
                return {"status": 200 if ok else 500}
            case "mac.lock_screen":
                return {"status": 200 if self.adapter.lock_screen() else 500}
            case "mac.set_volume":
                level = max(0, min(100, int(envelope.args["level"])))
                return {"status": 200 if self.adapter.set_volume(level) else 500, "level": level}
            case "mac.media":
                return self._media(str(envelope.args["app"]), str(envelope.args["command"]))
            case "mac.type_text":
                return self._type_text(envelope.args["bundle_id"], str(envelope.args["text"]))
            case "mac.press_key":
                return self._press_key(
                    envelope.args["bundle_id"],
                    str(envelope.args["key"]),
                    list(envelope.args.get("modifiers") or []),
                )
            case "mac.capture_screen" | "mac.describe_screen":
                return self._capture_screen(envelope.job_id)
            case "mac.system_info":
                return {"status": 200, **self.adapter.system_info()}
            case "mac.say":
                ok = self.adapter.say(str(envelope.args["text"])[:600])
                return {"status": 200 if ok else 500}
            case "mac.click":
                x, y = int(envelope.args["x"]), int(envelope.args["y"])
                ok = self.adapter.click(x, y)
                return {"status": 200 if ok else 500, "x": x, "y": y}
            case "mac.send_file":
                return self._send_file(
                    envelope.args["path"], envelope.args["scope_bookmark"], envelope.job_id
                )
            case "mac.whatsapp_send":
                return self._whatsapp_send(str(envelope.args["phone"]), str(envelope.args["text"]))
            case "mac.set_setting":
                return self._set_setting(str(envelope.args["key"]), str(envelope.args["value"]))
            case _:
                raise ValueError(f"unhandled action {envelope.action}")

    # ── the hand ───────────────────────────────────────────────────────
    def _set_setting(self, key: str, value: str) -> dict[str, Any]:
        """Allowlisted keys only; the observation is the setting re-read, never the
        command's exit code (PLAN.md 10.4.1)."""
        key, value = key.lower(), value.strip().lower()
        if key not in SETTINGS:
            return {"status": 400, "error": f"{key} is not a setting this Mac exposes"}
        if key in ("wifi", "bluetooth", "dark_mode", "do_not_disturb") and value not in (
            "on", "off", "true", "false", "1", "0"
        ):
            return {"status": 400, "error": "value must be on or off"}
        state = self.adapter.set_setting(key, value)
        if state is None:
            return {"status": 501, "key": key, "value": value, "error": "not available here"}
        return {"status": 200, "key": key, "value": value, "state": state}

    def _open_url(self, url: str) -> dict[str, Any]:
        opened = self.adapter.open_url(url)
        window = self.adapter.frontmost_window()
        return {
            "opened": opened,
            "opened_url": url,
            "frontmost_bundle_id": window.frontmost_bundle_id,
        }

    def _scoped(self, path: str, scope_bookmark: str) -> Path | None:
        """The path, if and only if it lives inside the granted directory."""
        try:
            root = Path(scope_bookmark).expanduser().resolve()
            target = Path(path).expanduser().resolve()
        except (OSError, RuntimeError):
            return None
        return target if target.is_relative_to(root) else None

    def _open_file(self, path: str, scope_bookmark: str) -> dict[str, Any]:
        target = self._scoped(path, scope_bookmark)
        if target is None:
            return {"status": 403, "path": path, "reason": "outside the granted directory"}
        if not self.adapter.file_exists(str(target), scope_bookmark):
            return {"status": 404, "path": path}
        return {"status": 200 if self.adapter.open_path(str(target)) else 500, "path": str(target)}

    def _find_files(self, query: str, scope_bookmark: str) -> dict[str, Any]:
        if self._scoped(scope_bookmark, scope_bookmark) is None:
            return {"status": 403, "matches": []}
        matches = self.adapter.find_files(query, scope_bookmark)
        return {"status": 200, "matches": matches[:50], "count": len(matches)}

    def _media(self, app: str, command: str) -> dict[str, Any]:
        # Both are allowlists, not free text: they end up inside an AppleScript.
        if app not in MEDIA_APPS or command not in MEDIA_COMMANDS:
            return {"status": 400, "reason": "unknown app or command"}
        return {"status": 200 if self.adapter.media(app, command) else 500}

    def _type_text(self, bundle_id: str, text: str) -> dict[str, Any]:
        if not self.adapter.accessibility_granted():
            return {"permission": "accessibility_denied", "typed_chars": 0}
        self.adapter.activate(bundle_id)
        window = self._wait_for_frontmost(bundle_id)
        if window.frontmost_bundle_id != bundle_id:
            # Never type into whatever happens to be in front instead.
            return {
                "permission": "granted",
                "typed_chars": 0,
                "frontmost_bundle_id": window.frontmost_bundle_id,
            }
        typed = self.adapter.keystroke(text)
        window = self.adapter.frontmost_window()
        return {
            "permission": "granted",
            "typed_chars": len(text) if typed else 0,
            "frontmost_bundle_id": window.frontmost_bundle_id,
        }

    def _press_key(self, bundle_id: str, key: str, modifiers: list[str]) -> dict[str, Any]:
        if not self.adapter.accessibility_granted():
            return {"permission": "accessibility_denied", "pressed_key": None}
        self.adapter.activate(bundle_id)
        window = self._wait_for_frontmost(bundle_id)
        if window.frontmost_bundle_id != bundle_id:
            return {
                "permission": "granted",
                "pressed_key": None,
                "frontmost_bundle_id": window.frontmost_bundle_id,
            }
        pressed = self.adapter.key_press(key, modifiers)
        return {
            "permission": "granted",
            "pressed_key": key if pressed else None,
            "modifiers": modifiers,
            "frontmost_bundle_id": window.frontmost_bundle_id,
        }

    def _capture_screen(self, job_id: str) -> dict[str, Any]:
        if not self.adapter.screen_recording_granted():
            return {"permission": "screen_recording_denied", "digest": None}
        capture = self.adapter.capture_screen()
        if capture is None:
            return {"permission": "granted", "digest": None}
        artifact_id = self._upload(capture.path, kind="screenshot", job_id=job_id)
        return {
            "permission": "granted",
            "digest": capture.digest,
            "artifact_id": artifact_id,
            "width": capture.width,
            "height": capture.height,
        }

    def _send_file(self, path: str, scope_bookmark: str, job_id: str) -> dict[str, Any]:
        target = self._scoped(path, scope_bookmark)
        if target is None:
            return {"status": 403, "path": path, "reason": "outside the granted directory"}
        if not self.adapter.file_exists(str(target), scope_bookmark):
            return {"status": 404, "path": path}
        artifact_id = self._upload(str(target), kind="file", job_id=job_id)
        return {
            "status": 200 if artifact_id else 500,
            "path": str(target),
            "artifact_id": artifact_id,
        }

    def _whatsapp_send(self, phone: str, text: str) -> dict[str, Any]:
        """Open the chat with the text filled in, wait for WhatsApp, press Return.

        Three observations come back — the URL opened, WhatsApp frontmost, the key
        pressed — and the verifier needs all of them. Return is pressed only if
        WhatsApp is genuinely in front: pressing it into whatever else is there is the
        one thing this must never do.
        """
        digits = "".join(c for c in phone if c.isdigit())
        url = f"whatsapp://send?phone={digits}&text={quote(text)}"
        opened = self.adapter.open_url(url)
        observed: dict[str, Any] = {"opened": opened, "opened_url": url, "pressed_key": None}
        if not opened:
            return observed
        window = self._wait_for_frontmost(WHATSAPP)
        observed["frontmost_bundle_id"] = window.frontmost_bundle_id
        if window.frontmost_bundle_id != WHATSAPP or not self.adapter.accessibility_granted():
            return observed
        # WhatsApp needs a moment to fill the composer before Return means "send".
        time.sleep(1.0 if self.frontmost_timeout >= 1.0 else 0)
        if self.adapter.key_press("return", []):
            observed["pressed_key"] = "return"
        return observed

    def _upload(self, path: str, *, kind: str, job_id: str) -> str | None:
        if self.uploader is None:
            return None
        try:
            return self.uploader(path, kind=kind, job_id=job_id)
        except Exception as exc:  # noqa: BLE001 — reported as "no artifact", never a crash
            return None if exc else None

    def _read_ui(self, bundle_id: str) -> dict[str, Any]:
        if not self.adapter.accessibility_granted():
            return {"permission": "accessibility_denied", "elements": []}
        elements = self.adapter.ui_elements(bundle_id)
        return {
            "permission": "granted",
            "element_count": len(elements),
            "elements": [
                {"role": e.role, "title": e.title, "enabled": e.enabled} for e in elements
            ],
        }

    def _press_button(self, bundle_id: str, title: str) -> dict[str, Any]:
        if not self.adapter.accessibility_granted():
            return {"permission": "accessibility_denied", "pressed": False}
        pressed = self.adapter.press_button(bundle_id, title)
        window = self.adapter.frontmost_window()
        return {
            "permission": "granted",
            "pressed": pressed,
            "frontmost_bundle_id": window.frontmost_bundle_id,
            "window_title": window.window_title,
        }

    def _capture(self, bundle_id: str) -> dict[str, Any]:
        if not self.adapter.screen_recording_granted():
            return {"permission": "screen_recording_denied", "digest": None}
        capture = self.adapter.capture_window(bundle_id)
        if capture is None:
            return {"permission": "granted", "digest": None}
        # The digest travels; the pixels stay here unless something asks for them.
        return {
            "permission": "granted",
            "digest": capture.digest,
            "width": capture.width,
            "height": capture.height,
        }

    def _file_exists(self, path: str, scope_bookmark: str) -> dict[str, Any]:
        return {"exists": self.adapter.file_exists(path, scope_bookmark), "path": path}

    def _open_app(self, bundle_id: str) -> dict[str, Any]:
        """Launch, then wait for the window server to agree it is in front.

        Returning as soon as the launch call succeeds is what makes naive automation
        unreliable: the process exists long before its window does.
        """
        state = self.adapter.launch(bundle_id)
        if not state.is_running:
            return {"bundle_id": bundle_id, "is_running": False, "pid": None}

        window = self._wait_for_frontmost(bundle_id)
        return {
            "bundle_id": bundle_id,
            "pid": state.pid,
            "is_running": True,
            "frontmost_bundle_id": window.frontmost_bundle_id,
            "window_title": window.window_title,
        }

    def _focus_app(self, bundle_id: str) -> dict[str, Any]:
        state = self.adapter.activate(bundle_id)
        window = self._wait_for_frontmost(bundle_id)
        return {
            "bundle_id": bundle_id,
            "pid": state.pid,
            "is_running": state.is_running,
            "frontmost_bundle_id": window.frontmost_bundle_id,
            "window_title": window.window_title,
        }

    def _wait_for_frontmost(self, bundle_id: str, timeout: float | None = None):
        """Poll the window server until the app is in front, or the timeout expires.

        On timeout we return what we *did* see, rather than an error. Whatever is
        genuinely frontmost is the evidence, and the server decides what it means.
        """
        deadline = time.monotonic() + (timeout or self.frontmost_timeout)
        window = self.adapter.frontmost_window()
        while time.monotonic() < deadline:
            if window.frontmost_bundle_id == bundle_id:
                return window
            time.sleep(POLL_INTERVAL_SECONDS)
            window = self.adapter.frontmost_window()
        return window

    def _run_template(self, args: dict[str, Any]) -> dict[str, Any]:
        template = COMMAND_TEMPLATES[args["template"]]
        argv = template.render(args.get("params", {}))
        code, output = self.adapter.run_argv(argv, timeout=args.get("timeout", 60))
        # The exit code is reported as an observation, not as success. The action's
        # declared evidence decides whether this worked.
        return {"status": 200 if code == 0 else 500, "exit_code": code, "output": output[:2000]}

    def _sign(self, result: JobResult) -> JobResult:
        from jarvis.services.device.keys import sign

        signature = sign(self.device_private_pem, result.signing_payload())
        return JobResult(**{**result.__dict__, "signature": signature})


__all__ = ["Executor", "RejectReason"]
