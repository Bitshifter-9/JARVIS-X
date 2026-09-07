"""Text to speech: one neural voice for every surface.

The phone and the Mac each have a system voice, and neither sounds like an assistant
anyone would choose. Edge-TTS (already the video pipeline's voice) is free, keyless and
neural; serving it from here means the Mac, the phone and Alexa all speak with the same
voice, and a reply is synthesized once and cached by its text.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from jarvis.api.deps import CurrentUser
from jarvis.core.config import get_settings
from jarvis.core.logging import get_logger

log = get_logger(__name__)
router = APIRouter(prefix="/v1/tts", tags=["tts"])


class SpeakRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    voice: str | None = Field(default=None, max_length=64)


async def synthesize_to(text: str, voice: str, path: Path) -> None:
    """Edge-TTS → MP3 on disk. Isolated so tests can replace it."""
    import edge_tts

    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(str(path))


@router.post("")
async def speak(body: SpeakRequest, _user: CurrentUser) -> Any:  # noqa: ANN401
    """MP3 of the text. Cached by (voice, text), so a repeated line costs nothing."""
    settings = get_settings()
    voice = body.voice or settings.tts_voice
    key = hashlib.sha256(f"{voice}\n{body.text}".encode()).hexdigest()[:32]
    directory = Path(settings.artifact_dir).parent / "tts"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{key}.mp3"
    if not path.exists():  # noqa: ASYNC240 — one stat on local disk
        await synthesize_to(body.text, voice, path)
        log.info("tts_synthesized", voice=voice, chars=len(body.text))
    return FileResponse(path, media_type="audio/mpeg", filename="jarvis.mp3")


@router.get("/voices")
async def voices(_user: CurrentUser) -> dict[str, Any]:
    """A short list worth choosing from; ``edge-tts --list-voices`` has hundreds."""
    return {
        "default": get_settings().tts_voice,
        "suggested": [
            {"id": "en-GB-RyanNeural", "note": "British, calm — the classic JARVIS"},
            {"id": "en-US-AndrewMultilingualNeural", "note": "warm, very natural"},
            {"id": "en-US-BrianMultilingualNeural", "note": "deep, measured"},
            {"id": "en-IN-PrabhatNeural", "note": "Indian English"},
            {"id": "en-US-AvaMultilingualNeural", "note": "female, expressive"},
            {"id": "en-GB-SoniaNeural", "note": "female, British"},
        ],
    }
