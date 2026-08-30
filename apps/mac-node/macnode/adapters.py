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


@dataclass(frozen=True)
class UIElement:
    """One node of an application's accessibility tree."""

    role: str
    title: str | None = None
    value: str | None = None
    enabled: bool = True


@dataclass(frozen=True)
class CaptureResult:
    """A screenshot, recorded by digest rather than by content.

    The bytes stay on the Mac unless the action explicitly asked for an upload. A digest
    is enough to prove the screen was in a particular state without shipping a picture of
    the user's desktop to a server.
    """

    digest: str
    width: int
    height: int
    path: str | None = None


class MacAdapter(Protocol):
    def launch(self, bundle_id: str) -> AppState: ...
    def activate(self, bundle_id: str) -> AppState: ...
    def running(self, bundle_id: str) -> AppState: ...
    def frontmost_window(self) -> WindowState: ...
    def run_argv(self, argv: list[str], timeout: int) -> tuple[int, str]: ...
    def accessibility_granted(self) -> bool: ...
    def screen_recording_granted(self) -> bool: ...
    def ui_elements(self, bundle_id: str) -> list[UIElement]: ...
    def press_button(self, bundle_id: str, title: str) -> bool: ...
    def capture_window(self, bundle_id: str) -> CaptureResult | None: ...
    def file_exists(self, path: str, scope_bookmark: str) -> bool: ...


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

    # ── permissions ────────────────────────────────────────────────────
    def accessibility_granted(self) -> bool:
        """Whether this process may drive other apps.

        Checked before every AX action rather than assumed: the permission is revoked by
        any code-signing change, and the failure is otherwise silent — calls simply
        return nothing, which reads as "the button was not there".
        """
        try:
            from ApplicationServices import AXIsProcessTrusted  # noqa: PLC0415

            return bool(AXIsProcessTrusted())
        except ImportError:
            return False

    def screen_recording_granted(self) -> bool:
        try:
            from Quartz import CGPreflightScreenCaptureAccess  # noqa: PLC0415

            return bool(CGPreflightScreenCaptureAccess())
        except ImportError:
            return False

    # ── accessibility tree ─────────────────────────────────────────────
    def _ax_app(self, bundle_id: str):
        from ApplicationServices import AXUIElementCreateApplication  # noqa: PLC0415

        state = self.running(bundle_id)
        if state.pid is None:
            return None
        return AXUIElementCreateApplication(state.pid)

    def ui_elements(self, bundle_id: str) -> list[UIElement]:
        from ApplicationServices import (  # noqa: PLC0415
            AXUIElementCopyAttributeValue,
            kAXChildrenAttribute,
            kAXEnabledAttribute,
            kAXRoleAttribute,
            kAXTitleAttribute,
            kAXValueAttribute,
            kAXWindowsAttribute,
        )

        app = self._ax_app(bundle_id)
        if app is None:
            return []

        def attribute(element, name):
            error, value = AXUIElementCopyAttributeValue(element, name, None)
            return None if error else value

        elements: list[UIElement] = []

        def walk(element, depth: int = 0) -> None:
            # Depth-bounded: a full tree of a large app is tens of thousands of nodes,
            # and nothing we do needs more than the visible controls.
            if depth > 4 or len(elements) > 200:
                return
            elements.append(
                UIElement(
                    role=str(attribute(element, kAXRoleAttribute) or "unknown"),
                    title=_as_text(attribute(element, kAXTitleAttribute)),
                    value=_as_text(attribute(element, kAXValueAttribute)),
                    enabled=bool(attribute(element, kAXEnabledAttribute) or False),
                )
            )
            for child in attribute(element, kAXChildrenAttribute) or []:
                walk(child, depth + 1)

        for window in attribute(app, kAXWindowsAttribute) or []:
            walk(window)
        return elements

    def press_button(self, bundle_id: str, title: str) -> bool:
        from ApplicationServices import (  # noqa: PLC0415
            AXUIElementCopyAttributeValue,
            AXUIElementPerformAction,
            kAXChildrenAttribute,
            kAXPressAction,
            kAXTitleAttribute,
            kAXWindowsAttribute,
        )

        app = self._ax_app(bundle_id)
        if app is None:
            return False

        def attribute(element, name):
            error, value = AXUIElementCopyAttributeValue(element, name, None)
            return None if error else value

        def find(element, depth: int = 0):
            if depth > 4:
                return None
            if _as_text(attribute(element, kAXTitleAttribute)) == title:
                return element
            for child in attribute(element, kAXChildrenAttribute) or []:
                if found := find(child, depth + 1):
                    return found
            return None

        for window in attribute(app, kAXWindowsAttribute) or []:
            if target := find(window):
                return AXUIElementPerformAction(target, kAXPressAction) == 0
        return False

    # ── screen evidence ────────────────────────────────────────────────
    def capture_window(self, bundle_id: str) -> CaptureResult | None:
        """Capture one window, never the whole screen.

        Bounded to the app the action named, so evidence for "did Chrome open" cannot
        incidentally photograph a password manager sitting behind it.
        """
        import hashlib  # noqa: PLC0415
        import tempfile  # noqa: PLC0415

        if not self.screen_recording_granted():
            return None

        state = self.running(bundle_id)
        if state.pid is None:
            return None

        from Quartz import (  # noqa: PLC0415
            CGWindowListCopyWindowInfo,
            kCGNullWindowID,
            kCGWindowListOptionOnScreenOnly,
        )

        windows = CGWindowListCopyWindowInfo(
            kCGWindowListOptionOnScreenOnly, kCGNullWindowID
        ) or []
        target = next(
            (w for w in windows if int(w.get("kCGWindowOwnerPID", -1)) == state.pid), None
        )
        if target is None:
            return None

        window_id = int(target.get("kCGWindowNumber", 0))
        bounds = target.get("kCGWindowBounds", {})
        path = tempfile.NamedTemporaryFile(suffix=".png", delete=False).name
        code, _ = self.run_argv(
            ["/usr/sbin/screencapture", "-x", "-o", "-l", str(window_id), path], timeout=15
        )
        if code != 0:
            return None

        with open(path, "rb") as handle:
            digest = hashlib.sha256(handle.read()).hexdigest()
        return CaptureResult(
            digest=f"sha256:{digest}",
            width=int(bounds.get("Width", 0)),
            height=int(bounds.get("Height", 0)),
            path=path,
        )

    # ── scoped files ───────────────────────────────────────────────────
    def file_exists(self, path: str, scope_bookmark: str) -> bool:
        """Look only inside a directory the user chose.

        No full-disk access. ``scope_bookmark`` is the directory the user granted; a path
        that escapes it is refused rather than resolved.
        """
        from pathlib import Path  # noqa: PLC0415

        try:
            root = Path(scope_bookmark).expanduser().resolve()
            target = Path(path).expanduser().resolve()
        except (OSError, RuntimeError):
            return False
        if not target.is_relative_to(root):
            return False
        return target.exists()


def _as_text(value) -> str | None:  # noqa: ANN001
    if value is None:
        return None
    text = str(value)
    return text or None


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
    accessibility: bool = True
    screen_recording: bool = True
    elements: dict[str, list[UIElement]] = field(default_factory=dict)
    pressed: list[tuple[str, str]] = field(default_factory=list)
    capture: CaptureResult | None = None
    scoped_files: set[str] = field(default_factory=set)
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

    def accessibility_granted(self) -> bool:
        return self.accessibility

    def screen_recording_granted(self) -> bool:
        return self.screen_recording

    def ui_elements(self, bundle_id: str) -> list[UIElement]:
        return self.elements.get(bundle_id, []) if self.accessibility else []

    def press_button(self, bundle_id: str, title: str) -> bool:
        if not self.accessibility:
            return False
        available = {e.title for e in self.elements.get(bundle_id, []) if e.enabled}
        if title not in available:
            return False
        self.pressed.append((bundle_id, title))
        return True

    def capture_window(self, bundle_id: str) -> CaptureResult | None:
        if not self.screen_recording or bundle_id not in self.running_apps:
            return None
        return self.capture or CaptureResult(digest="sha256:" + "0" * 64, width=1440, height=900)

    def file_exists(self, path: str, scope_bookmark: str) -> bool:
        from pathlib import PurePosixPath

        if not PurePosixPath(path).is_relative_to(PurePosixPath(scope_bookmark)):
            return False
        return path in self.scoped_files
