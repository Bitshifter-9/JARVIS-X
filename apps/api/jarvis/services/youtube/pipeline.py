"""The YouTube Shorts pipeline: research → script → voice → b-roll → render.

Every stage is free-tier: DuckDuckGo for research (no key), the LLM router for the
script, Edge-TTS for the voiceover — whose word-boundary events give caption timings
for free, so there is no Whisper here — Pexels for b-roll (optional key; without it
the video is captions on a dark background), and ffmpeg for the render.

The upload is deliberately NOT here. Publishing is an R2 external effect, so it goes
through the tool gateway as ``youtube.upload`` and waits for a human approval.
"""

from __future__ import annotations

import asyncio
import json
import re
import shlex
import shutil
import sys
import uuid
from pathlib import Path
from typing import Any

import httpx
from jarvis.core.config import get_settings
from jarvis.core.logging import get_logger
from jarvis.llm.router import LLMRouter
from jarvis.llm.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

log = get_logger(__name__)

PEXELS_API = "https://api.pexels.com/videos/search"

SCRIPT_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "description": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "search_terms": {"type": "array", "items": {"type": "string"}},
        "style": {"type": "string"},
        "script": {"type": "string"},
    },
    "required": ["title", "description", "tags", "search_terms", "style", "script"],
}

# Requested length, parsed straight out of the topic text ("2 min video", "90 sec").
DEFAULT_SECONDS = 55
_DURATION = re.compile(r"(\d+)\s*(min(?:ute)?s?|sec(?:ond)?s?)\b", re.I)


def target_seconds(topic: str) -> int:
    match = _DURATION.search(topic)
    if not match:
        return DEFAULT_SECONDS
    value = int(match.group(1))
    seconds = value * 60 if match.group(2).lower().startswith("min") else value
    return max(30, min(seconds, 600))


def is_landscape(seconds: int) -> bool:
    """Under ~2 minutes is a vertical Short; longer is a regular 16:9 video."""
    return seconds >= 120


def frame_size(seconds: int) -> tuple[int, int]:
    return (1920, 1080) if is_landscape(seconds) else (1080, 1920)


ART_STYLE_WORDS = (
    "cartoon", "anime", "illustration", "comic", "pixel", "drawing",
    "painted", "watercolor", "3d render", "claymation",
)


def wants_art_style(style: str) -> bool:
    """Stock footage can't be a cartoon — an artistic style forces generated images."""
    lowered = style.lower()
    return any(word in lowered for word in ART_STYLE_WORDS)


# A style the user names explicitly is not a suggestion. Weaker models drop it from
# the request, so it is detected here and overrides whatever the model returned.
STYLE_OVERRIDES: tuple[tuple[str, str], ...] = (
    ("anime", "Japanese anime illustration, vibrant, cel shaded"),
    ("cartoon", "2D cartoon illustration, bold clean lines, bright flat colors"),
    ("comic", "comic book illustration, bold ink lines, halftone shading"),
    ("pixel", "pixel art, 16-bit, crisp pixels"),
    ("watercolor", "watercolor painting, soft washes"),
    ("3d render", "polished 3D render, soft studio lighting"),
    ("claymation", "claymation stop-motion character, soft clay texture"),
    ("realistic", "photorealistic cinematic photography"),
    ("photo", "clean cinematic photography"),
)


def style_from_topic(topic: str, fallback: str) -> str:
    lowered = topic.lower()
    for keyword, style in STYLE_OVERRIDES:
        if keyword in lowered:
            return style
    return fallback


def system_prompt(seconds: int) -> str:
    words = int(seconds * 2.4)  # calibration knob: ~145 wpm speaking rate
    shape = (
        "a regular 16:9 YouTube video" if is_landscape(seconds)
        else "a vertical YouTube Short"
    )
    # One visual per ~6 seconds: a still held longer than that reads as a broken
    # video, however good the picture is.
    terms = max(3, min(20, seconds // 6))
    return (
        f"You write voiceover scripts for {shape} about {seconds} seconds long.\n"
        f"- 'script' is ONLY the spoken words, {int(words * 0.9)}-{int(words * 1.1)} "
        "words of plain text: no emojis, no stage directions, no hashtags, no camera notes.\n"
        "- Open with a hook in the first sentence; end with a one-line takeaway.\n"
        "- 'title' is under 90 characters and curiosity-driven without lying.\n"
        "- 'description' is 2-3 sentences, then 3-5 hashtags on the last line.\n"
        "- 'tags' is 10-15 one-or-two-word tags.\n"
        f"- 'search_terms' is {terms} short visual queries, one per section of the "
        "script, in order (e.g. 'robot assembling phone', 'stock chart rising').\n"
        "- 'style' is a short visual style phrase for the imagery. If the topic asks "
        "for a style (cartoon, anime, realistic…), use exactly that; otherwise pick "
        "one that fits, e.g. 'clean cinematic photography'.\n"
        "RESEARCH NOTES ARE MANDATORY SOURCE MATERIAL. When they are provided, the "
        "script must report the SPECIFIC stories in them — name the companies, "
        "products and numbers they contain, and say when each happened. Never write "
        "generic filler like 'technology is changing our lives'. Never invent a fact "
        "that is not in the notes."
    )

# libass style burned onto the video. PlayRes defaults to 384x288, so sizes are small.
SUBTITLE_STYLE = (
    "FontName=Arial,FontSize=14,Bold=1,PrimaryColour=&H00FFFFFF,"
    "OutlineColour=&H00000000,BorderStyle=1,Outline=2,Shadow=0,Alignment=2,MarginV=110"
)


# ── user assets (avatar mode) ──────────────────────────────────────────
# One voice sample, one silent "hero" video, and one background-music track per
# user, uploaded once via POST /v1/youtube/assets. Their presence is what switches
# the pipeline's behaviour — there is no separate mode flag to drift out of sync.
ASSET_KINDS = ("voice", "avatar", "music")


def asset_dir(user_id: Any) -> Path:
    return Path(get_settings().youtube_workdir) / "assets" / str(user_id)


def find_assets(user_id: Any) -> dict[str, Path | None]:
    d = asset_dir(user_id)
    return {kind: next(iter(d.glob(f"{kind}.*")), None) for kind in ASSET_KINDS}


def f5_available() -> bool:
    return shutil.which("f5-tts_infer-cli") is not None


def ffmpeg_bin() -> str:
    return get_settings().ffmpeg_path


def ffprobe_bin() -> str:
    path = get_settings().ffmpeg_path
    return str(Path(path).with_name("ffprobe")) if "/" in path else "ffprobe"


# ── research ───────────────────────────────────────────────────────────
QUERY_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {"type": "string"},
        "is_news": {"type": "boolean"},
    },
    "required": ["query", "is_news"],
}

QUERY_PROMPT = (
    "Turn the user's video request into a web search query for RESEARCHING the "
    "subject.\n"
    "- 'query' contains ONLY the subject to research. Strip every production "
    "instruction: length ('2 min'), format ('video', 'short'), visual style "
    "('using cartoons', 'anime'), and verbs like 'make'/'find'/'explain'. "
    "Example: 'find latest tech news and make a 2 min video using cartoons' -> "
    "'latest technology news'.\n"
    "- 'is_news' is true when the subject is current events, news, releases or "
    "anything where recency matters."
)

# Conservative fallback when the model is unavailable: drop production phrases only.
_PRODUCTION_NOISE = re.compile(
    r"\b\d+\s*(?:min(?:ute)?s?|sec(?:ond)?s?)\b"
    r"|\busing\s+\w+(?:\s+(?:style|animation))?"
    r"|\b(?:make|create|generate|render|produce|find|get)\b"
    r"|\b(?:a|an|the)?\s*(?:video|short|reel|clip)\b"
    r"|\bexplaining it\b|\babout it\b",
    re.I,
)


def strip_production_words(topic: str) -> str:
    cleaned = _PRODUCTION_NOISE.sub(" ", topic)
    cleaned = re.sub(r"\s+(and|with|for)\s*$", "", cleaned.strip(), flags=re.I)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" ,.-")
    return cleaned or topic


async def plan_research(
    router: LLMRouter, topic: str, *, user_id: uuid.UUID | None = None
) -> tuple[str, bool]:
    """What to actually search for, and whether it needs fresh news.

    Searching the raw request is what produced cartoon-clipart results for
    "find latest tech news ... using cartoons" — the style words dominated the query.
    """
    try:
        response = await router.extract(
            [Message("system", QUERY_PROMPT), Message("user", topic)],
            QUERY_SCHEMA,
            user_id=user_id,
            prompt_version="youtube-query-v1",
            max_tokens=1024,
        )
        data = response.parsed or _lenient_json(response.text)
        query = str(data.get("query", "")).strip()
        if query:
            return query, bool(data.get("is_news", False))
    except Exception as exc:  # noqa: BLE001 — falls back to the heuristic
        log.warning("youtube_query_plan_failed", error=str(exc)[:200])
    cleaned = strip_production_words(topic)
    return cleaned, bool(re.search(r"\b(news|latest|today|update)\b", topic, re.I))


async def research(
    topic: str, *, max_results: int = 6, is_news: bool = False
) -> list[str]:
    """Dated headline + snippet per result.

    News topics go to the news index with a recency window, so the script can cite
    real, current stories instead of whatever evergreen page ranks highest.
    """

    def _search() -> list[str]:
        from ddgs import DDGS

        with DDGS() as ddgs:
            rows: list[dict] = []
            if is_news:
                rows = ddgs.news(topic, max_results=max_results, timelimit="w")
            if not rows:
                rows = ddgs.text(topic, max_results=max_results)
            notes = []
            for r in rows:
                date = str(r.get("date", ""))[:10]
                stamp = f"[{date}] " if date else ""
                source = r.get("source", "")
                notes.append(
                    f"{stamp}{r.get('title', '')}"
                    f"{f' ({source})' if source else ''}: {r.get('body', '')}"
                )
            return notes

    try:
        return await asyncio.wait_for(asyncio.to_thread(_search), timeout=45)
    except Exception as exc:  # noqa: BLE001 — research is best-effort by design
        log.warning("youtube_research_failed", topic=topic, error=str(exc)[:200])
        return []


# ── script ─────────────────────────────────────────────────────────────
async def write_script(
    router: LLMRouter,
    topic: str,
    notes: list[str],
    *,
    user_id: uuid.UUID | None = None,
    winners: list[str] | None = None,
    seconds: int = DEFAULT_SECONDS,
) -> dict[str, Any]:
    from jarvis.services.event.service import EventService

    prompt = f"Topic: {topic}"
    if winners:
        prompt += (
            "\n\nThis channel's best-performing videos so far — bias the angle and "
            "hook style toward what worked:\n" + "\n".join(f"- {w}" for w in winners)
        )
    if notes:
        prompt += "\n\nResearch notes:\n" + EventService.untrusted("\n".join(notes))

    response = await router.extract(
        [Message("system", system_prompt(seconds)), Message("user", prompt)],
        SCRIPT_SCHEMA,
        user_id=user_id,
        prompt_version="youtube-script-v2",
        max_tokens=4096,
    )
    data = response.parsed or _lenient_json(response.text)
    if not data.get("title") or not data.get("script"):
        raise ValueError(f"script generation returned no title/script: {str(data)[:200]}")
    data.setdefault("description", data["title"])
    data.setdefault("tags", [])
    data.setdefault("search_terms", [topic])
    data["style"] = style_from_topic(
        topic, str(data.get("style") or "clean cinematic photography")
    )
    return data


def _lenient_json(text: str) -> dict[str, Any]:
    """Models occasionally fence their JSON despite the schema. Unfence and parse."""
    cleaned = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", text.strip())
    return json.loads(cleaned)


# ── voice + captions ───────────────────────────────────────────────────
async def synthesize(script: str, voice: str, workdir: Path) -> tuple[Path, Path, float]:
    """Voiceover and SRT captions in one pass over the Edge-TTS stream."""
    import edge_tts

    audio = workdir / "voice.mp3"
    subs = workdir / "captions.srt"

    communicate = edge_tts.Communicate(script, voice, boundary="WordBoundary")
    submaker = edge_tts.SubMaker()
    with audio.open("wb") as f:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] in ("WordBoundary", "SentenceBoundary"):
                submaker.feed(chunk)
    subs.write_text(submaker.get_srt(), encoding="utf-8")
    return audio, subs, await media_duration(audio)


async def synthesize_cloned(script: str, voice_ref: Path, workdir: Path) -> tuple[Path, float]:
    """The script in the user's own voice, via F5-TTS zero-shot cloning.

    Subprocess rather than import: the model holds gigabytes that should leave RAM
    the moment the clip exists. The reference is transcribed here with faster-whisper
    and passed as --ref_text — leaving it to F5-TTS routes through transformers' ASR
    pipeline, which needs torchcodec and ffmpeg 4-7 shared libraries.
    First run downloads the model (~1.3 GB).
    """
    ref_words = await _whisper_words(voice_ref)
    audio = workdir / "voice.wav"
    await _run([
        "f5-tts_infer-cli", "--model", "F5TTS_v1_Base",
        "--ref_audio", str(voice_ref),
        "--ref_text", " ".join(text for _, _, text in ref_words),
        "--gen_text", script,
        "-o", str(workdir), "-w", audio.name,
    ])
    if not audio.exists():
        raise RuntimeError("f5-tts reported success but produced no audio file")
    return audio, await media_duration(audio)


async def _whisper_words(audio: Path) -> list[tuple[float, float, str]]:
    def _transcribe() -> list[tuple[float, float, str]]:
        from faster_whisper import WhisperModel

        model = WhisperModel("tiny", device="cpu", compute_type="int8")
        segments, _ = model.transcribe(str(audio), word_timestamps=True)
        return [
            (w.start, w.end, w.word.strip())
            for segment in segments
            for w in (segment.words or [])
        ]

    return await asyncio.to_thread(_transcribe)


async def captions_from_audio(audio: Path, workdir: Path) -> Path:
    """Word-timed SRT for cloned audio, which has no boundary events to ride on."""
    subs = workdir / "captions.srt"
    subs.write_text(_srt_from_words(await _whisper_words(audio)), encoding="utf-8")
    return subs


def _srt_from_words(words: list[tuple[float, float, str]]) -> str:
    def ts(seconds: float) -> str:
        ms = round(seconds * 1000)
        return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"

    return "\n".join(
        f"{i}\n{ts(start)} --> {ts(end)}\n{text}\n"
        for i, (start, end, text) in enumerate(words, 1)
    )


async def media_duration(path: Path) -> float:
    out = await _run(
        [ffprobe_bin(), "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)]
    )
    return float(out.strip())


# ── lip-sync (avatar mode) ─────────────────────────────────────────────
async def lipsync(avatar: Path, audio: Path, workdir: Path) -> Path:
    """Run the configured lip-sync command on the hero video + cloned audio."""
    out = workdir / "talking.mp4"
    template = get_settings().youtube_lipsync_cmd
    cmd = [
        part.format(video=avatar, audio=audio, out=out)
        for part in shlex.split(template)
    ]
    await _run(cmd)
    if not out.exists():
        raise RuntimeError(f"lip-sync command produced no {out.name}")
    return out


# ── generated imagery ──────────────────────────────────────────────────
# Local SDXL first: the free hosted tiers cap around 576px, which upscales into the
# soft, blocky frames that made earlier renders look cheap. Local runs at SDXL's
# native buckets (768x1344 / 1344x768).
LOCAL_IMAGE_CAP = 8


def local_images_available() -> bool:
    from huggingface_hub.constants import HF_HUB_CACHE

    if get_settings().image_backend == "remote":
        return False
    try:
        import diffusers  # noqa: F401
    except ImportError:
        return False
    weights = Path(HF_HUB_CACHE) / "models--stabilityai--stable-diffusion-xl-base-1.0"
    # The snapshot exists long before it is complete; require the UNet itself.
    return any(weights.glob("snapshots/*/unet/*.safetensors"))


async def generate_images_local(
    prompts: list[str], workdir: Path, size: tuple[int, int]
) -> list[Path]:
    """SDXL + Lightning in a subprocess. Returns [] so callers can fall back."""
    timeout = get_settings().local_image_timeout_minutes * 60
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "scripts.sdxl_generate",
        "--out-dir", str(workdir), "--size", f"{size[0]}x{size[1]}",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(json.dumps(prompts).encode()), timeout=timeout
        )
    except TimeoutError:
        process.kill()
        log.warning("local_images_timed_out", minutes=timeout // 60)
        return []
    if process.returncode != 0:
        log.warning("local_images_failed", error=stderr.decode(errors="replace")[-400:])
        return []
    return [Path(p) for p in json.loads(stdout)]


async def generate_images(
    prompts: list[str], workdir: Path, size: tuple[int, int]
) -> list[Path]:
    """One image per prompt, forming the video's slideshow.

    Best-effort throughout: local generation falls back to the hosted services, a
    failed prompt is skipped, and an empty result degrades to the gradient
    background. Nothing here ever fails the render.
    """
    settings = get_settings()
    wanted = prompts[:20]

    if local_images_available():
        # Each local image costs a minute or so of GPU; the Ken Burns push makes a
        # longer hold per still watchable, so fewer-but-sharp beats many-but-soft.
        if images := await generate_images_local(wanted[:LOCAL_IMAGE_CAP], workdir, size):
            log.info("youtube_images_generated", backend="local-sdxl", got=len(images))
            return images
        log.warning("local_images_empty_falling_back")

    images: list[Path] = []
    async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
        for i, prompt in enumerate(wanted):
            path = workdir / f"image{i}.png"
            ok = False
            if settings.huggingface_api_key:
                ok = await _hf_image(client, prompt, size, path)
            if not ok:
                ok = await _pollinations_image(client, prompt, size, path)
            if ok:
                images.append(path)
    log.info(
        "youtube_images_generated",
        backend="remote", requested=len(wanted), got=len(images),
    )
    return images


async def _hf_image(
    client: httpx.AsyncClient, prompt: str, size: tuple[int, int], path: Path
) -> bool:
    s = get_settings()
    url = f"https://router.huggingface.co/hf-inference/models/{s.huggingface_image_model}"
    try:
        response = await client.post(
            url,
            headers={"Authorization": f"Bearer {s.huggingface_api_key}"},
            json={"inputs": f"{prompt}, no text, no watermark"},
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        log.warning("hf_image_failed", error=str(exc)[:200])
        return False
    if not response.headers.get("content-type", "").startswith("image/"):
        log.warning("hf_image_failed", error=response.text[:200])
        return False
    await asyncio.to_thread(path.write_bytes, response.content)
    return True


async def _pollinations_image(
    client: httpx.AsyncClient, prompt: str, size: tuple[int, int], path: Path
) -> bool:
    """Keyless image generation — the reason videos have pictures with zero setup."""
    from urllib.parse import quote

    try:
        response = await client.get(
            f"https://image.pollinations.ai/prompt/{quote(prompt + ', no text')}",
            params={"width": size[0], "height": size[1], "nologo": "true"},
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        log.warning("pollinations_image_failed", error=str(exc)[:200])
        return False
    if not response.headers.get("content-type", "").startswith("image/"):
        return False
    await asyncio.to_thread(path.write_bytes, response.content)
    return True


# ── b-roll ─────────────────────────────────────────────────────────────
async def fetch_broll(
    terms: list[str], need_seconds: float, workdir: Path, *, orientation: str = "portrait"
) -> list[Path]:
    key = get_settings().pexels_api_key
    if not key:
        return []

    clips: list[Path] = []
    covered = 0.0
    async with httpx.AsyncClient(
        timeout=60, headers={"Authorization": key}, follow_redirects=True
    ) as client:
        for term in terms:
            if covered >= need_seconds:
                break
            try:
                r = await client.get(
                    PEXELS_API,
                    params={"query": term, "orientation": orientation, "per_page": 2},
                )
                r.raise_for_status()
            except httpx.HTTPError as exc:
                log.warning("pexels_search_failed", term=term, error=str(exc)[:200])
                continue
            for video in r.json().get("videos", []):
                link = _best_file(video)
                if link is None:
                    continue
                path = workdir / f"clip{len(clips)}.mp4"
                try:
                    path.write_bytes((await client.get(link)).content)
                except httpx.HTTPError as exc:
                    log.warning("pexels_download_failed", error=str(exc)[:200])
                    continue
                clips.append(path)
                covered += float(video.get("duration") or 5)
                if covered >= need_seconds:
                    break
    log.info("youtube_broll_fetched", clips=len(clips), covered_seconds=round(covered, 1))
    return clips


def _best_file(video: dict) -> str | None:
    files = sorted(
        (f for f in video.get("video_files", []) if f.get("link")),
        key=lambda f: f.get("height") or 0,
    )
    if not files:
        return None
    good = [f for f in files if 1280 <= (f.get("height") or 0) <= 2048]
    return (good or files)[-1]["link"]


# ── render ─────────────────────────────────────────────────────────────
_subtitles_available: bool | None = None


async def subtitles_available() -> bool:
    """Whether this ffmpeg build has the libass ``subtitles`` filter.

    Stripped builds (some Homebrew bottles) lack it; the render then skips burned
    captions rather than failing, and the .srt still sits next to the video.
    """
    global _subtitles_available
    if _subtitles_available is None:
        filters = await _run([ffmpeg_bin(), "-hide_banner", "-filters"])
        _subtitles_available = " subtitles " in filters
        if not _subtitles_available:
            log.warning(
                "ffmpeg_missing_subtitles_filter",
                hint="install an ffmpeg built with libass to burn captions",
            )
    return _subtitles_available


MUSIC_VOLUME = 0.15  # calibration knob: BGM level relative to the voiceover
KEN_BURNS_MAX = 1.18  # how far the slow push travels over one still


def build_render_cmd(
    clips: list[Path],
    audio: Path,
    subs: Path | None,
    duration: float,
    out: Path,
    *,
    music: Path | None = None,
    images: list[Path] | None = None,
    size: tuple[int, int] = (1080, 1920),
) -> list[str]:
    """One ffmpeg invocation: normalize visuals to ``size``, concat, pad by cloning
    the last frame if they run short, trim to the voiceover, burn captions, and mix
    low-volume background music under the voice.

    ``subs=None`` skips the caption burn (ffmpeg without libass). Without clips,
    generated ``images`` become an evenly-timed slideshow; with neither, an animated
    gradient carries the captions.
    """
    w, h = size
    scale = f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},fps=30,setsar=1"
    sub_filter = (
        f",subtitles=filename='{subs.as_posix()}':force_style='{SUBTITLE_STYLE}'"
        if subs is not None
        else ""
    )
    tail = f"tpad=stop_mode=clone:stop_duration={duration:.2f}," \
           f"trim=duration={duration:.2f},setpts=PTS-STARTPTS{sub_filter}[out]"

    if clips:
        cmd = [ffmpeg_bin(), "-y"]
        for clip in clips:
            cmd += ["-i", str(clip)]
        cmd += ["-i", str(audio)]
        n = len(clips)
        voice_idx, music_idx = n, n + 1
        graph = "".join(f"[{i}:v]{scale}[v{i}];" for i in range(n))
        graph += "".join(f"[v{i}]" for i in range(n))
        graph += f"concat=n={n}:v=1:a=0[cat];[cat]{tail}"
    elif images:
        # Slideshow with a slow Ken Burns push, alternating in and out. Stills that
        # simply sit there are what made earlier renders look broken.
        seg = duration / len(images)
        frames = max(2, int((seg + 0.5) * 30))
        cmd = [ffmpeg_bin(), "-y"]
        for img in images:
            cmd += ["-loop", "1", "-t", f"{seg + 0.5:.2f}", "-i", str(img)]
        cmd += ["-i", str(audio)]
        n = len(images)
        voice_idx, music_idx = n, n + 1
        graph = ""
        for i in range(n):
            # Oversample before zooming: zoompan on a target-sized frame visibly
            # judders, because its pan offsets quantise to whole source pixels.
            zoom = (
                f"min(1+0.00035*on,{KEN_BURNS_MAX})" if i % 2 == 0
                else f"max({KEN_BURNS_MAX}-0.00035*on,1)"
            )
            graph += (
                f"[{i}:v]scale={w * 2}:{h * 2}:force_original_aspect_ratio=increase,"
                f"crop={w * 2}:{h * 2},"
                f"zoompan=z='{zoom}':d=1:fps=30:s={w}x{h}:"
                "x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)',"
                f"trim=end_frame={frames},setpts=PTS-STARTPTS,setsar=1[v{i}];"
            )
        graph += "".join(f"[v{i}]" for i in range(n))
        graph += f"concat=n={n}:v=1:a=0[cat];[cat]{tail}"
    else:
        # A slowly drifting gradient rather than a flat color — the no-asset, no-key
        # fallback should still look deliberate.
        cmd = [
            ffmpeg_bin(), "-y",
            "-f", "lavfi",
            "-i", f"gradients=s={w}x{h}:c0=0x0b1220:c1=0x1e3a5f:speed=0.03:r=30:d={duration:.2f}",
            "-i", str(audio),
        ]
        voice_idx, music_idx = 1, 2
        graph = f"[0:v]{tail}"

    if music is not None:
        # -stream_loop repeats a short track for the whole video; amix ends with the
        # voice (duration=first) and normalize=0 keeps the voice at full level.
        cmd += ["-stream_loop", "-1", "-i", str(music)]
        graph += (
            f";[{music_idx}:a]volume={MUSIC_VOLUME}[bg];"
            f"[{voice_idx}:a][bg]amix=inputs=2:duration=first:normalize=0[aout]"
        )
        audio_map = "[aout]"
    else:
        audio_map = f"{voice_idx}:a"

    cmd += [
        "-filter_complex", graph, "-map", "[out]", "-map", audio_map,
        "-t", f"{duration:.2f}",
        "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart",
        str(out),
    ]
    return cmd


async def _run(cmd: list[str]) -> str:
    process = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        raise RuntimeError(f"{cmd[0]} failed: {stderr.decode(errors='replace')[-800:]}")
    return stdout.decode(errors="replace")


async def past_winners(session: AsyncSession, user_id: Any) -> list[str]:
    """Top performers from the latest analytics sweep, for the script prompt.

    The analytics job's result row IS the storage — no extra table.
    """
    from sqlalchemy import text as sql

    try:
        row = (
            await session.execute(
                sql("""
                    SELECT result FROM jobs
                    WHERE user_id = :u AND kind = 'youtube.analytics'
                      AND status = 'succeeded' AND result ? 'videos'
                    ORDER BY id DESC LIMIT 1
                """),
                {"u": str(user_id)},
            )
        ).scalar()
    except Exception as exc:  # noqa: BLE001 — feedback is a bonus, never a blocker
        log.warning("past_winners_lookup_failed", error=str(exc)[:200])
        return []
    if not row:
        return []
    return [
        f"{v['title']} — {v['views']} views"
        for v in row.get("videos", [])[:5]
        if v.get("views", 0) > 0
    ]


# ── the whole thing ────────────────────────────────────────────────────
async def generate(
    session: AsyncSession, *, user_id: uuid.UUID, topic: str, job_id: Any
) -> dict[str, Any]:
    """Render a Short for ``topic``. Returns metadata plus the finished file's path."""
    settings = get_settings()
    workdir = Path(settings.youtube_workdir) / str(job_id)
    workdir.mkdir(parents=True, exist_ok=True)

    wanted = target_seconds(topic)
    size = frame_size(wanted)
    orientation = "landscape" if is_landscape(wanted) else "portrait"

    router = LLMRouter(session)
    query, is_news = await plan_research(router, topic, user_id=user_id)
    notes = await research(query, is_news=is_news)
    log.info(
        "youtube_research_done", query=query, is_news=is_news, notes=len(notes)
    )

    winners = await past_winners(session, user_id)
    draft = await write_script(
        router, topic, notes, user_id=user_id, winners=winners, seconds=wanted,
    )
    log.info(
        "youtube_script_ready",
        title=draft["title"], target_seconds=wanted, orientation=orientation,
    )

    assets = find_assets(user_id)

    # Voice: the user's cloned voice when a sample is uploaded and F5-TTS is
    # installed (`uv sync --extra avatar`); Edge-TTS otherwise.
    if assets["voice"] is not None and f5_available():
        audio, seconds = await synthesize_cloned(draft["script"], assets["voice"], workdir)
        subs = await captions_from_audio(audio, workdir)
        voice_mode = "cloned"
    else:
        audio, subs, seconds = await synthesize(
            draft["script"], settings.youtube_tts_voice, workdir
        )
        voice_mode = "edge-tts"

    # Visuals: lip-synced hero video when configured; otherwise b-roll; otherwise a
    # plain background. A lip-sync failure falls back rather than killing the daily
    # video — the approval preview shows what was actually rendered before anything
    # is published.
    clips: list[Path] = []
    visual_mode = "broll"
    if assets["avatar"] is not None and settings.youtube_lipsync_cmd:
        try:
            clips = [await lipsync(assets["avatar"], audio, workdir)]
            visual_mode = "avatar"
        except Exception as exc:  # noqa: BLE001
            log.warning("youtube_lipsync_failed", error=str(exc)[:300])
    images: list[Path] = []
    if not clips:
        # An explicitly artistic style ("cartoons", "anime") cannot come from stock
        # footage — generated imagery takes priority over Pexels in that case.
        if not wants_art_style(draft["style"]):
            clips = await fetch_broll(
                list(draft["search_terms"])[:8], seconds, workdir, orientation=orientation
            )
        visual_mode = "broll" if clips else "background"
        if not clips:
            # One image per script section, styled per the request.
            prompts = [f"{term}, {draft['style']}" for term in draft["search_terms"]]
            images = await generate_images(prompts, workdir, size)
            visual_mode = "generated-images" if images else "background"

    out = workdir / "final.mp4"
    burn = subs if await subtitles_available() else None
    await _run(
        build_render_cmd(
            clips, audio, burn, seconds, out,
            music=assets["music"], images=images, size=size,
        )
    )
    log.info(
        "youtube_video_rendered",
        path=str(out), seconds=round(seconds, 1), orientation=orientation,
        voice=voice_mode, visuals=visual_mode, captions_burned=burn is not None,
    )

    return {
        "title": draft["title"],
        "description": draft["description"],
        "tags": draft["tags"],
        "script": draft["script"],
        "style": draft["style"],
        "video_path": str(out),
        "seconds": round(seconds, 1),
        "target_seconds": wanted,
        "orientation": orientation,
        "voice": voice_mode,
        "visuals": visual_mode,
        "images": len(images),
        "music": assets["music"] is not None,
        "research_notes": len(notes),
    }
