"""The macOS boundary.

Every OS call lives behind ``MacAdapter`` for two reasons:

1. The validation and verification logic — the part that decides whether an action is
   allowed and whether it worked — can then be tested on any machine, in CI, without a Mac.
2. The real adapter is the only place PyObjC is imported, so a missing framework is one
   clear failure rather than an import error scattered through the helper.

``PyObjCAdapter`` calls the identical Apple APIs Swift would: ``NSWorkspace``,
``NSRunningApplication``, ``CGWindowListCopyWindowInfo``. Swift becomes relevant only for
shipping a signed, notarized ``.app`` — a packaging concern, not a capability one.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class AppState:
    """What was observed about an application. Not a verdict — the server decides that."""

    bundle_id: str
    pid: int | None = None
    is_running: bool = False
    is_frontmost: bool = False


@dataclass(frozen=True)
class WindowState:
    frontmost_bundle_id: str | None = None
    window_title: str | None = None
    pid: int | None = None


class MacAdapter(Protocol):
    def launch(self, bundle_id: str) -> AppState: ...
    def activate(self, bundle_id: str) -> AppState: ...
    def running(self, bundle_id: str) -> AppState: ...
    def frontmost_window(self) -> WindowState: ...
    def run_argv(self, argv: list[str], timeout: int) -> tuple[int, str]: ...


class PyObjCAdapter:
    """The real thing. Imports PyObjC lazily so this module loads anywhere."""

    def _workspace(self):
        from AppKit import NSWorkspace  # noqa: PLC0415

        return NSWorkspace.sharedWorkspace()

    def _running_app(self, bundle_id: str):
        from AppKit import NSRunningApplication  # noqa: PLC0415

        apps = NSRunningApplication.runningApplicationsWithBundleIdentifier_(bundle_id)
        return apps[0] if apps else None

    def launch(self, bundle_id: str) -> AppState:
        workspace = self._workspace()
        url = workspace.URLForApplicationWithBundleIdentifier_(bundle_id)
        if url is None:
            return AppState(bundle_id=bundle_id, is_running=False)

        from AppKit import NSWorkspaceOpenConfiguration  # noqa: PLC0415

        workspace.openApplicationAtURL_configuration_completionHandler_(
            url, NSWorkspaceOpenConfiguration.configuration(), None
        )
        return self.running(bundle_id)

    def activate(self, bundle_id: str) -> AppState:
        app = self._running_app(bundle_id)
        if app is None:
            return AppState(bundle_id=bundle_id, is_running=False)
        # NSApplicationActivateIgnoringOtherApps
        app.activateWithOptions_(1 << 1)
        return self.running(bundle_id)

    def running(self, bundle_id: str) -> AppState:
        app = self._running_app(bundle_id)
        if app is None:
            return AppState(bundle_id=bundle_id, is_running=False)
        return AppState(
            bundle_id=bundle_id,
            pid=int(app.processIdentifier()),
            is_running=not app.isTerminated(),
            is_frontmost=bool(app.isActive()),
        )

    def frontmost_window(self) -> WindowState:
        """Read the window server directly.

        ``NSWorkspace.frontmostApplication`` reports which app is active;
        ``CGWindowListCopyWindowInfo`` reports what is actually on screen in front. The
        second is the stronger claim, and it is the one the evidence records.
        """
        from AppKit import NSWorkspace  # noqa: PLC0415
        from Quartz import (  # noqa: PLC0415
            CGWindowListCopyWindowInfo,
            kCGNullWindowID,
            kCGWindowListExcludeDesktopElements,
            kCGWindowListOptionOnScreenOnly,
        )

        front = NSWorkspace.sharedWorkspace().frontmostApplication()
        if front is None:
            return WindowState()

        pid = int(front.processIdentifier())
        bundle_id = str(front.bundleIdentifier() or "")
        title = None

        windows = CGWindowListCopyWindowInfo(
            kCGWindowListOptionOnScreenOnly | kCGWindowListExcludeDesktopElements,
            kCGNullWindowID,
        ) or []
        for window in windows:
            if int(window.get("kCGWindowOwnerPID", -1)) == pid:
                title = window.get("kCGWindowName") or None
                break

        return WindowState(frontmost_bundle_id=bundle_id, window_title=title, pid=pid)

    def run_argv(self, argv: list[str], timeout: int) -> tuple[int, str]:
        """Run a rendered command template.

        ``shell=False``, always. The argv comes from a registered template with its slots
        filled as whole entries, so there is no string for a shell to reinterpret — which
        is precisely what v1's ``run_command`` got wrong.
        """
        try:
            completed = subprocess.run(  # noqa: S603 — argv from a registered template, no shell
                argv, capture_output=True, text=True, timeout=timeout, check=False
            )
        except subprocess.TimeoutExpired:
            return 124, "timed out"
        return completed.returncode, (completed.stdout or completed.stderr)[:4000]


@dataclass
class FakeMacAdapter:
    """A scriptable stand-in, so the helper's logic is testable without a Mac."""

    installed: set[str] = field(default_factory=set)
    running_apps: dict[str, AppState] = field(default_factory=dict)
    frontmost: str | None = None
    window_title: str | None = None
    command_result: tuple[int, str] = (0, "")
    launched: list[str] = field(default_factory=list)
    commands: list[list[str]] = field(default_factory=list)
    # Set when an app can be launched but refuses to come forward — the exact condition
    # the repair graph exists to handle.
    refuse_frontmost: set[str] = field(default_factory=set)

    def launch(self, bundle_id: str) -> AppState:
        self.launched.append(bundle_id)
        if bundle_id not in self.installed:
            return AppState(bundle_id=bundle_id, is_running=False)
        state = AppState(
            bundle_id=bundle_id, pid=4242, is_running=True,
            is_frontmost=bundle_id not in self.refuse_frontmost,
        )
        self.running_apps[bundle_id] = state
        if bundle_id not in self.refuse_frontmost:
            self.frontmost = bundle_id
        return state

    def activate(self, bundle_id: str) -> AppState:
        if bundle_id not in self.running_apps:
            return AppState(bundle_id=bundle_id, is_running=False)
        self.refuse_frontmost.discard(bundle_id)
        self.frontmost = bundle_id
        state = AppState(bundle_id=bundle_id, pid=4242, is_running=True, is_frontmost=True)
        self.running_apps[bundle_id] = state
        return state

    def running(self, bundle_id: str) -> AppState:
        return self.running_apps.get(bundle_id, AppState(bundle_id=bundle_id))

    def frontmost_window(self) -> WindowState:
        if self.frontmost is None:
            return WindowState()
        return WindowState(
            frontmost_bundle_id=self.frontmost, window_title=self.window_title, pid=4242
        )

    def run_argv(self, argv: list[str], timeout: int) -> tuple[int, str]:  # noqa: ARG002
        self.commands.append(argv)
        return self.command_result
