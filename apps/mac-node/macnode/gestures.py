"""Hand gestures on the Mac (PLAN.md 10.8): the webcam as an input, on-device.

The vocabulary is four gestures and each is an *input to the same gate*: a thumbs-up
decides the one pending approval exactly as the button does, an open palm stops the
voice, a pinch mutes. Nothing new becomes possible; it becomes touchless. No frame ever
leaves the Mac — MediaPipe runs here and only a gesture name comes out.

``GestureLoop`` is a state machine over plain callables (frames, classify, act), so the
debounce and the cooldown are tested with fakes; ``run_gestures`` wires the real camera.
"""

from __future__ import annotations

import math
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

GESTURES = ("open_palm", "thumbs_up", "thumbs_down", "pinch")

# MediaPipe hand landmark indices.
WRIST, THUMB_TIP, INDEX_TIP, MIDDLE_TIP, RING_TIP, PINKY_TIP = 0, 4, 8, 12, 16, 20
INDEX_PIP, MIDDLE_PIP, RING_PIP, PINKY_PIP = 6, 10, 14, 18
THUMB_IP = 3


def _dist(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    return math.dist(a[:2], b[:2])


def classify_landmarks(points: list[tuple[float, float, float]] | None) -> str | None:
    """Twenty-one (x, y, z) landmarks, image coordinates with y growing downward →
    one of ``GESTURES`` or ``None``. Heuristics, deliberately strict: a gesture that is
    not obvious is no gesture."""
    if not points or len(points) < 21:
        return None
    wrist = points[WRIST]
    size = _dist(wrist, points[MIDDLE_PIP]) or 1e-6  # palm length as the scale

    def extended(tip: int, pip: int) -> bool:
        return _dist(points[tip], wrist) > _dist(points[pip], wrist) * 1.15

    fingers = [
        extended(INDEX_TIP, INDEX_PIP),
        extended(MIDDLE_TIP, MIDDLE_PIP),
        extended(RING_TIP, RING_PIP),
        extended(PINKY_TIP, PINKY_PIP),
    ]
    pinch = _dist(points[THUMB_TIP], points[INDEX_TIP]) < 0.35 * size
    if pinch and not any(fingers[1:]):
        return "pinch"
    if all(fingers):
        return "open_palm"
    if not any(fingers):
        thumb_up = points[THUMB_TIP][1] < points[THUMB_IP][1] - 0.3 * size
        thumb_down = points[THUMB_TIP][1] > points[THUMB_IP][1] + 0.3 * size
        if thumb_up and points[THUMB_TIP][1] < wrist[1]:
            return "thumbs_up"
        if thumb_down and points[THUMB_TIP][1] > wrist[1]:
            return "thumbs_down"
    return None


@dataclass
class GestureLoop:
    """Debounce: a gesture must be held for ``hold_frames`` consecutive frames, then it
    fires once and is ignored for ``cooldown_seconds``."""

    frames: Callable[[], Any]  # → landmarks (whatever classify accepts), or None
    classify: Callable[[Any], str | None]
    act: Callable[[str], None]
    hold_frames: int = 6
    cooldown_seconds: float = 3.0
    clock: Callable[[], float] = time.monotonic
    _current: str | None = None
    _held: int = 0
    _last_fired: dict[str, float] = field(default_factory=dict)

    def run_once(self) -> str | None:
        """Consume one frame. Returns the gesture that fired, if one did."""
        gesture = self.classify(self.frames())
        if gesture != self._current:
            self._current, self._held = gesture, 0
        if gesture is None:
            return None
        self._held += 1
        if self._held != self.hold_frames:
            return None
        now = self.clock()
        if now - self._last_fired.get(gesture, -math.inf) < self.cooldown_seconds:
            return None
        self._last_fired[gesture] = now
        self.act(gesture)
        return gesture

    def run_forever(self, *, sleep: Callable[[float], None] = time.sleep) -> None:
        while True:
            try:
                self.run_once()
            except KeyboardInterrupt:
                raise
            except Exception:  # noqa: BLE001, S110 — a bad frame must not end the loop
                pass
            sleep(0.05)


class GestureActions:
    """What each gesture does, through the same doors as a tap."""

    def __init__(self, api: str, access_token: str, *, run_argv=subprocess.run) -> None:  # noqa: ANN001
        self.api = api.rstrip("/")
        self.token = access_token
        self.run_argv = run_argv
        self.log: list[str] = []

    def __call__(self, gesture: str) -> None:
        if gesture == "open_palm":
            # Stop whatever is being spoken. `say` is what the voice loop uses.
            self.run_argv(["/usr/bin/pkill", "-x", "say"], check=False, timeout=5)
            self.log.append("stopped speech")
        elif gesture in ("thumbs_up", "thumbs_down"):
            self.log.append(self._decide(approved=gesture == "thumbs_up"))
        elif gesture == "pinch":
            self.run_argv(
                ["/usr/bin/osascript", "-e", "set volume output muted true"], check=False, timeout=5
            )
            self.log.append("muted")

    def _decide(self, *, approved: bool) -> str:
        """Decide the one pending approval — and only if there is exactly one, because a
        gesture cannot point at a card."""
        import httpx

        headers = {"Authorization": f"Bearer {self.token}"}
        pending = httpx.get(f"{self.api}/v1/approvals", headers=headers, timeout=10).json()
        if len(pending) != 1:
            return f"{len(pending)} approvals pending; a gesture decides only when there is one"
        approval_id = pending[0]["id"]
        httpx.post(
            f"{self.api}/v1/approvals/{approval_id}/decision",
            headers=headers,
            json={"approved": approved, "decided_by": "gesture"},
            timeout=10,
        )
        return f"{'approved' if approved else 'rejected'} {approval_id[:8]}"


def run_gestures(*, api: str, access_token: str, camera: int = 0) -> None:
    """The real thing: MediaPipe Hands over the webcam. ``uv sync --extra gestures``."""
    try:
        import cv2
        import mediapipe as mp
    except ImportError as exc:
        raise SystemExit(
            "MediaPipe is not installed. Run:\n    uv sync --extra gestures\n"
            "then again: python -m macnode gestures"
        ) from exc

    hands = mp.solutions.hands.Hands(max_num_hands=1, min_detection_confidence=0.7)
    capture = cv2.VideoCapture(camera)

    def frames():  # noqa: ANN202
        ok, frame = capture.read()
        if not ok:
            return None
        result = hands.process(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        if not result.multi_hand_landmarks:
            return None
        return [(p.x, p.y, p.z) for p in result.multi_hand_landmarks[0].landmark]

    actions = GestureActions(api, access_token)

    def act(gesture: str) -> None:
        actions(gesture)
        print(f"{gesture}: {actions.log[-1]}")

    GestureLoop(frames=frames, classify=classify_landmarks, act=act).run_forever()
