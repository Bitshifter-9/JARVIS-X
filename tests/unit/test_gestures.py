"""Gestures (PLAN.md 10.8): the debounce, the cooldown, the classifier, and what each
gesture is allowed to do."""

from __future__ import annotations

from macnode.gestures import GestureActions, GestureLoop, classify_landmarks


def _loop(script, acted, *, clock):
    frames = iter(script)
    return GestureLoop(
        frames=lambda: next(frames, None),
        classify=lambda g: g,
        act=acted.append,
        hold_frames=3,
        cooldown_seconds=2.0,
        clock=clock,
    )


def test_a_gesture_must_be_held_and_then_fires_once():
    acted: list[str] = []
    now = [0.0]
    loop = _loop(["thumbs_up"] * 5, acted, clock=lambda: now[0])
    fired = [loop.run_once() for _ in range(5)]
    assert fired == [None, None, "thumbs_up", None, None]
    assert acted == ["thumbs_up"]


def test_a_flicker_resets_the_hold():
    acted: list[str] = []
    script = ["open_palm", "open_palm", None, "open_palm", "open_palm", "open_palm"]
    loop = _loop(script, acted, clock=lambda: 0.0)
    for _ in range(6):
        loop.run_once()
    assert acted == ["open_palm"]


def test_the_cooldown_swallows_a_repeat_until_it_passes():
    acted: list[str] = []
    now = [0.0]
    script = ["pinch"] * 3 + [None] + ["pinch"] * 3 + [None] + ["pinch"] * 3
    loop = _loop(script, acted, clock=lambda: now[0])
    for i in range(11):
        if i == 8:
            now[0] = 5.0  # the cooldown has passed before the third hold
        loop.run_once()
    assert acted == ["pinch", "pinch"]


def _hand(*, fingers_out: bool, thumb: str = "side") -> list[tuple[float, float, float]]:
    """A synthetic hand in image coordinates: wrist at the bottom, fingers up."""
    pts = [(0.5, 0.9, 0.0)] * 21
    pts[10] = (0.5, 0.6, 0.0)  # middle pip: palm length 0.3
    for tip, pip in ((8, 6), (12, 10), (16, 14), (20, 18)):
        pts[pip] = (0.45 + tip * 0.005, 0.6, 0.0)
        pts[tip] = (0.45 + tip * 0.005, 0.3 if fingers_out else 0.62, 0.0)
    pts[3] = (0.3, 0.7, 0.0)  # thumb ip
    pts[4] = {
        "up": (0.3, 0.45, 0.0),
        "down": (0.3, 0.98, 0.0),
        "side": (0.25, 0.7, 0.0),
        "pinch": (0.49, 0.62, 0.0),
    }[thumb]
    return pts


def test_the_classifier_reads_the_four_gestures_and_nothing_else():
    assert classify_landmarks(_hand(fingers_out=True)) == "open_palm"
    assert classify_landmarks(_hand(fingers_out=False, thumb="up")) == "thumbs_up"
    assert classify_landmarks(_hand(fingers_out=False, thumb="down")) == "thumbs_down"
    assert classify_landmarks(_hand(fingers_out=False, thumb="pinch")) == "pinch"
    assert classify_landmarks(_hand(fingers_out=False, thumb="side")) is None
    assert classify_landmarks(None) is None
    assert classify_landmarks([(0, 0, 0)] * 5) is None


def test_a_thumb_decides_only_when_exactly_one_approval_is_pending(monkeypatch):
    calls: list[tuple[str, dict | None]] = []

    class Resp:
        def __init__(self, data):  # noqa: ANN001
            self._data = data

        def json(self):  # noqa: ANN202
            return self._data

    pending = [[{"id": "a1b2c3d4-0000"}]]

    def get(url, headers=None, timeout=None):  # noqa: ANN001
        calls.append(("GET", None))
        return Resp(pending[0])

    def post(url, headers=None, json=None, timeout=None):  # noqa: ANN001
        calls.append(("POST", json))
        return Resp({})

    import httpx

    monkeypatch.setattr(httpx, "get", get)
    monkeypatch.setattr(httpx, "post", post)
    ran: list[list[str]] = []
    actions = GestureActions(
        "https://api.test", "tok", run_argv=lambda argv, **kw: ran.append(argv)
    )

    actions("thumbs_up")
    assert calls[-1] == ("POST", {"approved": True, "decided_by": "gesture"})
    pending[0] = [{"id": "x"}, {"id": "y"}]
    actions("thumbs_down")
    assert calls[-1][0] == "GET" and "2 approvals pending" in actions.log[-1]
    actions("open_palm")
    assert ran[-1][:2] == ["/usr/bin/pkill", "-x"]
    actions("pinch")
    assert "muted" in ran[-1][-1]
