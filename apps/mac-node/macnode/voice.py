"""Always-on voice on the Mac: "Hey Jarvis" → listen → transcribe → ask → speak.

The loop is four pluggable parts and one state machine, so the state machine is tested
with fakes and the parts are swapped freely:

* **wake** — openWakeWord's pre-trained ``hey_jarvis`` model on the microphone. A wake
  model listens for one phrase; it does not transcribe the room, which is both cheaper
  and the more private choice.
* **listen** — record until ~0.8 s of silence (energy VAD), capped at 12 s.
* **transcribe** — faster-whisper, on this Mac. Audio never leaves the machine; only the
  text does, exactly as the app's chat box sends text.
* **ask** — ``POST /v1/chat`` with the user's bearer token — the same brain, same policy,
  same memory as every other surface. Anything effectful still becomes an approval.
* **speak** — macOS ``say``. Zero dependencies, and it is what the "visible indicator"
  requirement (PLAN.md 3.6) sounds like.

Everything audio is imported lazily; ``uv sync --extra voice`` installs it.
"""

from __future__ import annotations

import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

SAMPLE_RATE = 16_000
FRAME_SECONDS = 0.08  # openWakeWord expects 80 ms frames at 16 kHz
SILENCE_SECONDS = 0.8
MAX_UTTERANCE_SECONDS = 12.0
WAKE_THRESHOLD = 0.5
STOP_PHRASES = ("stop listening", "go to sleep", "never mind")


@dataclass
class VoiceLoop:
    """The state machine. Every collaborator is a plain callable."""

    wait_for_wake: Callable[[], None]
    listen: Callable[[], Any]  # → audio (whatever transcribe accepts)
    transcribe: Callable[[Any], str]
    ask: Callable[[str], str]
    speak: Callable[[str], None]
    indicate: Callable[[str], None] = lambda _state: None
    transcript: list[tuple[str, str]] = field(default_factory=list)

    def run_once(self) -> str | None:
        """One wake → reply cycle. Returns what was spoken, or ``None`` if nothing was."""
        self.indicate("idle")
        self.wait_for_wake()
        self.indicate("listening")
        audio = self.listen()
        heard = (self.transcribe(audio) or "").strip()
        if not heard:
            self.indicate("idle")
            return None
        if heard.lower().rstrip(".!?") in STOP_PHRASES:
            self.speak("Okay.")
            self.indicate("idle")
            return None
        self.indicate("thinking")
        reply = (self.ask(heard) or "").strip() or "I did not get a reply."
        self.transcript.append((heard, reply))
        self.indicate("speaking")
        self.speak(reply)
        self.indicate("idle")
        return reply

    def run_forever(self) -> None:
        while True:
            try:
                self.run_once()
            except KeyboardInterrupt:
                raise
            except Exception as exc:  # noqa: BLE001 — a bad frame must not end the assistant
                self.indicate("idle")
                self.speak("Something went wrong.") if False else None  # keep quiet, retry
                time.sleep(0.5)
                _ = exc


# ── the real parts ─────────────────────────────────────────────────────
class OpenWakeWord:
    """Blocks until the wake phrase is heard."""

    def __init__(self, model_name: str = "hey_jarvis") -> None:
        try:
            from openwakeword.model import Model
        except ImportError as exc:
            raise SystemExit(
                "openWakeWord is not installed. Run:\n"
                "    uv sync --extra voice && uv pip install openwakeword\n"
                "then again: python -m macnode voice"
            ) from exc

        self.model = Model(wakeword_models=[model_name], inference_framework="onnx")
        self.model_name = model_name

    def __call__(self) -> None:
        import numpy as np
        import sounddevice as sd

        frame = int(SAMPLE_RATE * FRAME_SECONDS)
        self.model.reset()
        with sd.InputStream(
            samplerate=SAMPLE_RATE, channels=1, dtype="int16", blocksize=frame
        ) as stream:
            while True:
                chunk, _ = stream.read(frame)
                scores = self.model.predict(np.frombuffer(chunk, dtype=np.int16))
                if max(scores.values(), default=0.0) >= WAKE_THRESHOLD:
                    return


def record_until_silence() -> Any:
    """Record from the microphone until the speaker pauses. Energy VAD; no model."""
    import numpy as np
    import sounddevice as sd

    frame = int(SAMPLE_RATE * FRAME_SECONDS)
    frames: list[Any] = []
    quiet = 0.0
    started = time.monotonic()
    threshold = None
    with sd.InputStream(
        samplerate=SAMPLE_RATE, channels=1, dtype="int16", blocksize=frame
    ) as stream:
        while time.monotonic() - started < MAX_UTTERANCE_SECONDS:
            chunk, _ = stream.read(frame)
            samples = np.frombuffer(chunk, dtype=np.int16).astype(np.float32)
            frames.append(samples)
            energy = float(np.sqrt(np.mean(samples**2)) + 1e-6)
            # The first few frames set the room's noise floor; speech must clear 3× it.
            if threshold is None and len(frames) >= 4:
                threshold = 3.0 * float(np.mean([np.sqrt(np.mean(f**2)) for f in frames]))
            if threshold is not None:
                quiet = quiet + FRAME_SECONDS if energy < threshold else 0.0
                if quiet >= SILENCE_SECONDS and len(frames) > 12:
                    break
    return np.concatenate(frames) / 32768.0


class Whisper:
    def __init__(self, size: str = "base") -> None:
        from faster_whisper import WhisperModel

        self.model = WhisperModel(size, compute_type="int8")

    def __call__(self, audio: Any) -> str:
        segments, _ = self.model.transcribe(audio, language="en", vad_filter=True)
        return " ".join(s.text.strip() for s in segments).strip()


class ChatAsker:
    """The same endpoint the app uses. The bearer token is the user's own session."""

    def __init__(self, api: str, access_token: str) -> None:
        self.api = api.rstrip("/")
        self.token = access_token
        self.history: list[dict[str, str]] = []

    def __call__(self, text: str) -> str:
        import httpx

        self.history.append({"role": "user", "content": text})
        response = httpx.post(
            f"{self.api}/v1/chat",
            headers={"Authorization": f"Bearer {self.token}"},
            json={"messages": self.history[-10:]},
            timeout=120,
        )
        response.raise_for_status()
        reply = str(response.json().get("text", ""))
        self.history.append({"role": "assistant", "content": reply})
        return reply


def say(text: str) -> None:
    subprocess.run(["/usr/bin/say", "-r", "190", text[:1500]], check=False)  # noqa: S603 — fixed argv


class ServerSpeaker:
    """The server's neural voice (``POST /v1/tts``), with macOS ``say`` as the fallback.

    The same voice the phone uses, so Jarvis sounds like one assistant. A network
    failure never silences it: the fallback is the built-in voice, not nothing.
    """

    def __init__(self, api: str, access_token: str, *, fetch=None, play=None) -> None:  # noqa: ANN001
        self.api = api.rstrip("/")
        self.token = access_token
        self._fetch = fetch or self._http_fetch
        self._play = play or self._afplay

    def __call__(self, text: str) -> None:
        try:
            audio = self._fetch(text)
        except Exception:  # noqa: BLE001 — offline, expired token: fall back, don't fail
            audio = None
        if not audio:
            say(text)
            return
        self._play(audio)

    def _http_fetch(self, text: str) -> bytes | None:
        import httpx

        response = httpx.post(
            f"{self.api}/v1/tts",
            headers={"Authorization": f"Bearer {self.token}"},
            json={"text": text[:2000]},
            timeout=30,
        )
        return response.content if response.status_code == 200 else None

    @staticmethod
    def _afplay(audio: bytes) -> None:
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as handle:
            handle.write(audio)
            path = handle.name
        subprocess.run(["/usr/bin/afplay", path], check=False)  # noqa: S603 — fixed argv


def indicate(state: str) -> None:
    """A visible and audible indicator: notification banner plus a short system sound."""
    labels = {"listening": "Listening…", "thinking": "Thinking…", "speaking": "Speaking"}
    if state in labels:
        subprocess.run(  # noqa: S603 — fixed argv, fixed text
            [
                "/usr/bin/osascript",
                "-e",
                f'display notification "{labels[state]}" with title "JARVIS"',
            ],
            check=False,
        )
    if state == "listening":
        subprocess.run(["/usr/bin/afplay", "/System/Library/Sounds/Tink.aiff"], check=False)  # noqa: S603


def run_voice(*, api: str, access_token: str, wake_model: str, whisper_model: str) -> None:
    VoiceLoop(
        wait_for_wake=OpenWakeWord(wake_model),
        listen=record_until_silence,
        transcribe=Whisper(whisper_model),
        ask=ChatAsker(api, access_token),
        speak=ServerSpeaker(api, access_token),
        indicate=indicate,
    ).run_forever()


__all__ = ["STOP_PHRASES", "ChatAsker", "ServerSpeaker", "VoiceLoop", "run_voice", "say"]


def run_ambient(
    *, api: str, access_token: str, device_id: str, whisper_model: str = "base"
) -> None:
    """Ambient transcription (#2): transcribe the room on this Mac and post only the *text*
    (the audio is transcribed locally and discarded). Consent-gated by being explicit, with a
    visible indicator; stop with Ctrl-C. Never runs silently."""
    import httpx

    whisper = Whisper(whisper_model)
    api = api.rstrip("/")
    print("🎙️  Ambient transcription ON — audio stays on this Mac and is discarded; only the "
          "text is stored to your account. Ctrl-C to stop.")
    while True:
        audio = record_until_silence()
        text = (whisper(audio) or "").strip()
        if len(text) < 8:
            continue
        try:
            httpx.post(
                f"{api}/v1/devices/{device_id}/transcript",
                headers={"Authorization": f"Bearer {access_token}"},
                json={"text": text, "kind": "ambient"},
                timeout=20,
            )
        except Exception:  # noqa: BLE001, S110 — keep listening; a dropped post is fine
            pass


def run_meeting(
    *, api: str, access_token: str, device_id: str, title: str = "Meeting",
    whisper_model: str = "base",
) -> None:
    """Meeting capture (#10): transcribe on this Mac until Ctrl-C, then save the transcript and
    let the server turn dated action items into tasks. Audio never leaves the Mac."""
    import httpx

    whisper = Whisper(whisper_model)
    api = api.rstrip("/")
    print(f"🎙️  Recording “{title}” on this Mac. Speak; Ctrl-C to end and save.")
    parts: list[str] = []
    try:
        while True:
            audio = record_until_silence()
            text = (whisper(audio) or "").strip()
            if text:
                parts.append(text)
                print("  …", text[:70])
    except KeyboardInterrupt:
        pass
    transcript = " ".join(parts).strip()
    if not transcript:
        print("Nothing captured.")
        return
    resp = httpx.post(
        f"{api}/v1/devices/{device_id}/transcript",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"text": transcript, "kind": "meeting", "title": title},
        timeout=30,
    )
    made = resp.json().get("tasks_created", 0) if resp.status_code < 400 else 0
    print(f"\nSaved the meeting. {made} action item(s) turned into tasks.")
