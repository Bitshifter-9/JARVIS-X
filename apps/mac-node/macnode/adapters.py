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
    # ── the hand: every verb below is one typed action, never a shell ──
    def open_url(self, url: str) -> bool: ...
    def open_path(self, path: str) -> bool: ...
    def keystroke(self, text: str) -> bool: ...
    def key_press(self, key: str, modifiers: list[str]) -> bool: ...
    def capture_screen(self) -> CaptureResult | None: ...
    def ocr_image(self, path: str) -> str: ...
    def clipboard_read(self) -> str: ...
    def clipboard_write(self, text: str) -> bool: ...
    def notify(self, title: str, body: str) -> bool: ...
    def lock_screen(self) -> bool: ...
    def set_volume(self, level: int) -> bool: ...
    def media(self, app: str, command: str) -> bool: ...
    def find_files(self, query: str, scope_bookmark: str) -> list[str]: ...
    def set_setting(self, key: str, value: str) -> str | None: ...
    def click(self, x: int, y: int) -> bool: ...
    def say(self, text: str) -> bool: ...
    def system_info(self) -> dict: ...


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

        windows = (
            CGWindowListCopyWindowInfo(
                kCGWindowListOptionOnScreenOnly | kCGWindowListExcludeDesktopElements,
                kCGNullWindowID,
            )
            or []
        )
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

        windows = CGWindowListCopyWindowInfo(kCGWindowListOptionOnScreenOnly, kCGNullWindowID) or []
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

    # ── the hand ───────────────────────────────────────────────────────
    def open_url(self, url: str) -> bool:
        from AppKit import NSURL  # noqa: PLC0415

        target = NSURL.URLWithString_(url)
        return bool(target is not None and self._workspace().openURL_(target))

    def open_path(self, path: str) -> bool:
        code, _ = self.run_argv(["/usr/bin/open", path], timeout=15)
        return code == 0

    def keystroke(self, text: str) -> bool:
        # Chunked: System Events drops characters on very long strings.
        for start in range(0, len(text), 200):
            chunk = applescript_quote(text[start : start + 200])
            code, _ = self._osascript(
                f'tell application "System Events" to keystroke "{chunk}"'
            )
            if code != 0:
                return False
        return True

    def key_press(self, key: str, modifiers: list[str]) -> bool:
        using = ", ".join(f"{m} down" for m in modifiers)
        suffix = f" using {{{using}}}" if using else ""
        if key in KEY_CODES:
            script = f'tell application "System Events" to key code {KEY_CODES[key]}{suffix}'
        else:
            script = (
                f'tell application "System Events" to keystroke "{applescript_quote(key)}"{suffix}'
            )
        code, _ = self._osascript(script)
        return code == 0

    def capture_screen(self) -> CaptureResult | None:
        import hashlib  # noqa: PLC0415
        import tempfile  # noqa: PLC0415

        if not self.screen_recording_granted():
            return None
        path = tempfile.NamedTemporaryFile(suffix=".png", delete=False).name
        code, _ = self.run_argv(["/usr/sbin/screencapture", "-x", path], timeout=15)
        if code != 0:
            return None
        with open(path, "rb") as handle:
            digest = hashlib.sha256(handle.read()).hexdigest()
        return CaptureResult(digest=f"sha256:{digest}", width=0, height=0, path=path)

    def ocr_image(self, path: str) -> str:
        """On-device OCR of a screenshot via the macOS Vision framework (screen memory #1).
        Returns the recognised text, or "" if Vision isn't available — the pixels never leave
        the Mac; only this text does. Requires pyobjc-framework-Vision."""
        try:
            import Quartz  # noqa: PLC0415
            import Vision  # noqa: PLC0415
            from Foundation import NSURL  # noqa: PLC0415

            url = NSURL.fileURLWithPath_(path)
            source = Quartz.CGImageSourceCreateWithURL(url, None)
            if source is None:
                return ""
            image = Quartz.CGImageSourceCreateImageAtIndex(source, 0, None)
            if image is None:
                return ""
            handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(image, None)
            request = Vision.VNRecognizeTextRequest.alloc().init()
            request.setRecognitionLevel_(1)  # accurate
            handler.performRequests_error_([request], None)
            lines: list[str] = []
            for obs in request.results() or []:
                candidate = obs.topCandidates_(1)
                if candidate:
                    lines.append(str(candidate[0].string()))
            return "\n".join(lines)
        except Exception:  # noqa: BLE001 — no Vision, no OCR; the loop just skips
            return ""

    def clipboard_read(self) -> str:
        _, output = self.run_argv(["/usr/bin/pbpaste"], timeout=5)
        return output

    def clipboard_write(self, text: str) -> bool:
        code, _ = self._osascript(f'set the clipboard to "{applescript_quote(text)}"')
        return code == 0

    def notify(self, title: str, body: str) -> bool:
        code, _ = self._osascript(
            f'display notification "{applescript_quote(body)}" '
            f'with title "{applescript_quote(title)}"'
        )
        return code == 0

    def lock_screen(self) -> bool:
        # Cmd-Ctrl-Q is the system lock shortcut; it needs no privilege escalation.
        code, _ = self._osascript(
            'tell application "System Events" to keystroke "q" using {command down, control down}'
        )
        return code == 0

    def set_volume(self, level: int) -> bool:
        code, _ = self._osascript(f"set volume output volume {max(0, min(100, int(level)))}")
        return code == 0

    def media(self, app: str, command: str) -> bool:
        code, _ = self._osascript(f'tell application "{applescript_quote(app)}" to {command}')
        return code == 0

    def find_files(self, query: str, scope_bookmark: str) -> list[str]:
        code, output = self.run_argv(
            ["/usr/bin/mdfind", "-onlyin", scope_bookmark, query], timeout=20
        )
        return [line for line in output.splitlines() if line][:50] if code == 0 else []

    def set_setting(self, key: str, value: str) -> str | None:
        """Change one allowlisted setting and return its state *re-read* afterwards —
        or ``None`` when this Mac has no way to do it (the evidence stays inconclusive
        rather than pretending). Every call is an argv list; nothing goes near a shell."""
        on = value in ("on", "true", "1")
        if key == "wifi":
            self.run_argv(
                ["/usr/sbin/networksetup", "-setairportpower", "en0", "on" if on else "off"],
                timeout=15,
            )
            code, out = self.run_argv(
                ["/usr/sbin/networksetup", "-getairportpower", "en0"], timeout=10
            )
            return None if code != 0 else ("on" if out.strip().lower().endswith("on") else "off")
        if key == "dark_mode":
            flag = "true" if on else "false"
            self._osascript(
                "tell application \"System Events\" to tell appearance preferences "
                f"to set dark mode to {flag}"
            )
            code, out = self._osascript(
                'tell application "System Events" to tell appearance preferences to get dark mode'
            )
            return None if code != 0 else ("on" if out.strip() == "true" else "off")
        if key == "bluetooth":
            # blueutil (brew) is the only scriptable switch; without it, inconclusive.
            code, _ = self.run_argv(["/opt/homebrew/bin/blueutil", "-p", "1" if on else "0"], 10)
            if code != 0:
                return None
            code, out = self.run_argv(["/opt/homebrew/bin/blueutil", "-p"], 10)
            return None if code != 0 else ("on" if out.strip() == "1" else "off")
        if key == "brightness":
            if not value.replace(".", "").isdigit():
                return None
            level = max(0, min(100, int(float(value))))
            code, _ = self.run_argv(["/opt/homebrew/bin/brightness", str(level / 100)], 10)
            if code != 0:
                return None
            code, out = self.run_argv(["/opt/homebrew/bin/brightness", "-l"], 10)
            for line in out.splitlines():
                if "brightness" in line:
                    try:
                        return str(round(float(line.split()[-1]) * 100))
                    except ValueError:
                        return None
            return None
        if key == "do_not_disturb":
            # macOS has no public switch; a Shortcut named "Jarvis DND On"/"Jarvis DND Off"
            # (one action each: Set Focus) is the documented bridge (docs/GO-LIVE.md).
            name = "Jarvis DND On" if on else "Jarvis DND Off"
            code, _ = self.run_argv(["/usr/bin/shortcuts", "run", name], 20)
            return ("on" if on else "off") if code == 0 else None
        return None

    def say(self, text: str) -> bool:
        code, _ = self.run_argv(["/usr/bin/say", text[:600]], timeout=30)
        return code == 0

    def system_info(self) -> dict:
        """Battery, load, free disk, uptime — four argv calls, parsed loosely."""
        import re

        info: dict = {}
        code, out = self.run_argv(["/usr/bin/pmset", "-g", "batt"], timeout=10)
        if code == 0 and (m := re.search(r"(\d+)%", out)):
            info["battery_percent"] = int(m.group(1))
            info["charging"] = "discharging" not in out.lower()
        code, out = self.run_argv(["/usr/sbin/sysctl", "-n", "vm.loadavg"], timeout=10)
        if code == 0 and (m := re.search(r"([\d.]+)", out)):
            info["load_1m"] = float(m.group(1))
        code, out = self.run_argv(["/bin/df", "-g", "/"], timeout=10)
        if code == 0 and len(out.splitlines()) > 1:
            parts = out.splitlines()[1].split()
            if len(parts) > 3 and parts[3].isdigit():
                info["disk_free_gb"] = int(parts[3])
        code, out = self.run_argv(["/usr/sbin/sysctl", "-n", "kern.boottime"], timeout=10)
        if code == 0 and (m := re.search(r"sec = (\d+)", out)):
            import time

            info["uptime_hours"] = round((time.time() - int(m.group(1))) / 3600, 1)
        return info

    def click(self, x: int, y: int) -> bool:
        """One left click at screen coordinates, through Quartz — R3, locally confirmed."""
        try:
            from Quartz import (  # noqa: PLC0415
                CGEventCreateMouseEvent,
                CGEventPost,
                kCGEventLeftMouseDown,
                kCGEventLeftMouseUp,
                kCGHIDEventTap,
                kCGMouseButtonLeft,
            )
        except ImportError:
            return False
        point = (float(x), float(y))
        for kind in (kCGEventLeftMouseDown, kCGEventLeftMouseUp):
            event = CGEventCreateMouseEvent(None, kind, point, kCGMouseButtonLeft)
            CGEventPost(kCGHIDEventTap, event)
        return True

    def _osascript(self, script: str) -> tuple[int, str]:
        """AppleScript as an argv entry. Nothing is ever interpolated into a shell."""
        return self.run_argv(["/usr/bin/osascript", "-e", script], timeout=15)


# Named keys System Events only accepts as codes.
KEY_CODES = {
    "return": 36,
    "enter": 76,
    "escape": 53,
    "tab": 48,
    "space": 49,
    "delete": 51,
    "forward_delete": 117,
    "up": 126,
    "down": 125,
    "left": 123,
    "right": 124,
    "home": 115,
    "end": 119,
    "page_up": 116,
    "page_down": 121,
}

MEDIA_APPS = {"Music", "Spotify"}
MEDIA_COMMANDS = {"play", "pause", "playpause", "next track", "previous track"}


def applescript_quote(text: str) -> str:
    """Escape for an AppleScript string literal.

    The text reaches ``osascript`` as one argv entry, so a shell never sees it; this is
    only about keeping a quote in the text from ending the AppleScript string early.
    """
    return text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


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
    ocr_text: str = ""
    scoped_files: set[str] = field(default_factory=set)
    # Set when an app can be launched but refuses to come forward — the exact condition
    # the repair graph exists to handle.
    refuse_frontmost: set[str] = field(default_factory=set)
    settings: dict[str, str] = field(default_factory=dict)
    unavailable_settings: set[str] = field(default_factory=set)
    clicks: list[tuple[int, int]] = field(default_factory=list)
    spoken: list[str] = field(default_factory=list)

    def launch(self, bundle_id: str) -> AppState:
        self.launched.append(bundle_id)
        if bundle_id not in self.installed:
            return AppState(bundle_id=bundle_id, is_running=False)
        state = AppState(
            bundle_id=bundle_id,
            pid=4242,
            is_running=True,
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

    # ── the hand ───────────────────────────────────────────────────────
    opened_urls: list[str] = field(default_factory=list)
    opened_paths: list[str] = field(default_factory=list)
    typed: list[str] = field(default_factory=list)
    keys: list[tuple[str, list[str]]] = field(default_factory=list)
    clipboard: str = ""
    notifications: list[tuple[str, str]] = field(default_factory=list)
    locked: int = 0
    volume: int | None = None
    media_calls: list[tuple[str, str]] = field(default_factory=list)
    file_index: dict[str, list[str]] = field(default_factory=dict)
    # Which bundle id the OS brings forward for a URL scheme (whatsapp:// → WhatsApp).
    url_handlers: dict[str, str] = field(default_factory=dict)
    refuse_urls: bool = False

    def open_url(self, url: str) -> bool:
        self.opened_urls.append(url)
        if self.refuse_urls:
            return False
        scheme = url.split(":", 1)[0]
        if handler := self.url_handlers.get(scheme):
            self.frontmost = handler
            self.running_apps[handler] = AppState(
                bundle_id=handler, pid=5150, is_running=True, is_frontmost=True
            )
        return True

    def open_path(self, path: str) -> bool:
        self.opened_paths.append(path)
        return True

    def keystroke(self, text: str) -> bool:
        if not self.accessibility:
            return False
        self.typed.append(text)
        return True

    def key_press(self, key: str, modifiers: list[str]) -> bool:
        if not self.accessibility:
            return False
        self.keys.append((key, list(modifiers)))
        return True

    def capture_screen(self) -> CaptureResult | None:
        return self.capture if self.screen_recording else None

    def ocr_image(self, path: str) -> str:
        return self.ocr_text

    def clipboard_read(self) -> str:
        return self.clipboard

    def clipboard_write(self, text: str) -> bool:
        self.clipboard = text
        return True

    def notify(self, title: str, body: str) -> bool:
        self.notifications.append((title, body))
        return True

    def lock_screen(self) -> bool:
        self.locked += 1
        return True

    def set_volume(self, level: int) -> bool:
        self.volume = level
        return True

    def media(self, app: str, command: str) -> bool:
        self.media_calls.append((app, command))
        return True

    def find_files(self, query: str, scope_bookmark: str) -> list[str]:
        return [p for p in self.file_index.get(scope_bookmark, []) if query.lower() in p.lower()]

    def set_setting(self, key: str, value: str) -> str | None:
        if key in self.unavailable_settings:
            return None
        self.settings[key] = value
        return value

    def click(self, x: int, y: int) -> bool:
        self.clicks.append((x, y))
        return True

    def say(self, text: str) -> bool:
        self.spoken.append(text)
        return True

    def system_info(self) -> dict:
        return {"battery_percent": 81, "charging": False, "load_1m": 1.2, "disk_free_gb": 120}

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
