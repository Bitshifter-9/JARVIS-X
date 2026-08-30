"""Executing admitted jobs, and observing what happened.

The helper reports **observations**, never a verdict. It says the pid it saw and which
bundle id is frontmost; the server's verifier decides whether that satisfies what the
action required. A device must not be able to declare its own success.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from jarvis.services.device.protocol import JobEnvelope, JobResult, RejectReason
from jarvis.services.tool_gateway.templates import COMMAND_TEMPLATES

from macnode.adapters import MacAdapter
from macnode.guard import GuardVerdict, JobGuard

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
                    job_id=envelope.job_id, status="failed",
                    error=f"{type(exc).__name__}: {exc}",
                )
            )

        return self._sign(
            JobResult(job_id=envelope.job_id, status="completed", observed=observed)
        )

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
            case _:
                raise ValueError(f"unhandled action {envelope.action}")

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
