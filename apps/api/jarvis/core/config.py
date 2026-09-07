"""Settings, loaded from the environment with a ``JARVIS_`` prefix.

Everything the system can spend money with, or reach the outside world through, is
configured here so it can be audited in one place. Defaults are the *safe* choice:
paid inference off, kill switch off, browser headless.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="JARVIS_", env_file=".env", extra="ignore", case_sensitive=False
    )

    # ── app ────────────────────────────────────────────────────────────
    env: Literal["local", "cloud", "demo", "test"] = "local"
    log_level: str = "INFO"
    base_url: str = "http://localhost:8000"
    timezone: str = "Asia/Kolkata"

    # ── database ───────────────────────────────────────────────────────
    database_url: str = "postgresql+asyncpg://jarvis:jarvis@localhost:5433/jarvis"
    db_echo: bool = False

    # ── auth ───────────────────────────────────────────────────────────
    # Placeholder, not a credential: startup refuses to run outside "local" with this value.
    jwt_secret: str = "dev-only-insecure-secret-change-me"  # noqa: S105
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = 15
    refresh_token_ttl_days: int = 30
    oauth_issuer: str = "http://localhost:8000"
    oauth_code_ttl_seconds: int = 120

    # ── LLM providers ──────────────────────────────────────────────────
    groq_api_key: str = ""
    # llama-3.3-70b-versatile was deprecated by Groq on 17 June 2026 and stopped being
    # served in August; gpt-oss-120b is their recommended replacement (free tier, fast).
    groq_model: str = "openai/gpt-oss-120b"
    gemini_api_key: str = ""
    # 2.5-flash is closed to keys issued after mid-2026; Google's error points here.
    gemini_model: str = "gemini-3.6-flash"
    openrouter_api_key: str = ""
    openrouter_free_model: str = "z-ai/glm-5.2:free"
    openrouter_paid_model: str = "anthropic/claude-haiku-4.5"
    ollama_host: str = "http://127.0.0.1:11434"
    ollama_model: str = "llama3.1:8b"
    # More gateways so a single free-tier quota wall never blocks everything.
    cerebras_api_key: str = ""
    cerebras_model: str = "cerebras/llama-3.3-70b"
    openrouter_free_model2: str = "meta-llama/llama-3.3-70b-instruct:free"
    # A generic OpenAI-compatible gateway (a LiteLLM proxy, vLLM, OpenAI, or any
    # OpenAI-shaped endpoint). Point one key at everything: it is tried first.
    gateway_base_url: str = ""
    gateway_api_key: str = ""
    gateway_model: str = "gpt-4o-mini"
    gateway_is_paid: bool = False
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"

    # ── budget ─────────────────────────────────────────────────────────
    # With paid inference off the system must still work end to end on free tiers
    # alone. tests/unit/test_llm_router.py asserts exactly that.
    enable_paid_llm: bool = False
    monthly_budget_inr: float = 2000.0
    llm_budget_inr: float = 800.0

    # ── agent budgets, enforced by the harness and never by a prompt ───
    max_steps: int = 8
    max_replans: int = 2
    max_tokens_per_run: int = 20_000
    max_run_seconds: int = 180
    max_notifications_per_day: int = 20
    max_calls_per_day: int = 3
    quiet_hours: str = "22:30-07:00"

    # ── queue ──────────────────────────────────────────────────────────
    job_visibility_timeout_seconds: int = 300
    job_max_attempts: int = 5
    scheduler_tick_seconds: float = 30.0
    # The proactive check (workers/heartbeat.py). Arithmetic over the goal engine, no
    # model; quiet hours and the daily cap still apply through the notification policy.
    heartbeat_minutes: float = 30.0
    # The focus guard (PLAN.md 10.6.4): inside a focus block you started, this many
    # minutes on a distracting app earns a nudge, then a spoken one, then the lock.
    # Where you are, for the weather in the morning briefing ("Hyderabad", "Bengaluru").
    owner_location: str = ""
    focus_guard_enabled: bool = False
    focus_guard_minutes: int = 10
    focus_distracting_apps: str = "youtube,instagram,reddit,twitter,x.com,tiktok,netflix,facebook"
    job_backoff_base_seconds: float = 2.0
    job_backoff_cap_seconds: float = 600.0

    # ── provider health ────────────────────────────────────────────────
    provider_cooldown_seconds: int = 60
    provider_failure_threshold: int = 3

    # ── Google ─────────────────────────────────────────────────────────
    google_client_id: str = ""
    google_client_secret: str = ""
    # How often every connected Gmail (and Classroom) is asked "what changed?". 60 s is
    # the demo path's 10-second event-to-alert budget; 14400 (4 h) is the quiet setting.
    gmail_poll_seconds: int = Field(
        default=14400, description="Seconds between connector scans (Gmail, Slack). 4h."
    )
    # The 'forget' tier: episodic memories never reinforced by recall are dropped after
    # this many days, to bound storage. 0 disables pruning. Semantic facts are kept.
    memory_episodic_ttl_days: int = Field(
        default=45, description="Days before an unused episodic memory is forgotten (0=never)"
    )
    slack_scan_enabled: bool = True

    # ── YouTube pipeline ───────────────────────────────────────────────
    # Homebrew's core ffmpeg dropped libass (no burned captions); point this at a
    # full build when one exists, e.g. /opt/homebrew/opt/ffmpeg-full/bin/ffmpeg.
    # ffprobe is resolved from the same directory.
    ffmpeg_path: str = "ffmpeg"
    # Pexels is optional: without a key, videos render captions on a dark background.
    pexels_api_key: str = ""
    # Free GPU via API: Hugging Face's inference free tier generates topic images
    # (FLUX.1-schnell) used as backgrounds when there is no avatar or b-roll —
    # and later, thumbnails. Free token: https://huggingface.co/settings/tokens
    huggingface_api_key: str = ""
    huggingface_image_model: str = "black-forest-labs/FLUX.1-schnell"
    # Where video imagery comes from. "auto" prefers local SDXL when its weights are
    # present (far higher resolution than the free hosted tiers, which cap around
    # 576px) and falls back to the keyless remote service otherwise.
    image_backend: Literal["auto", "local", "remote"] = "auto"
    # Minutes to allow for one local image batch before falling back.
    local_image_timeout_minutes: int = 25
    youtube_tts_voice: str = "en-US-ChristopherNeural"
    youtube_workdir: str = "var/youtube"
    # Uploads from an unaudited Google API project are forced private by YouTube
    # regardless of what is requested, so private is the honest default.
    youtube_privacy: str = "private"
    # Daily automation: with a topic set, the video worker enqueues one render, an
    # analytics refresh, and a comment sweep every day at daily_hour (settings.timezone).
    youtube_daily_topic: str = ""
    youtube_daily_hour: int = 9
    # Lip-sync is pluggable: a command template with {video} {audio} {out} placeholders
    # (e.g. a SadTalker/Wav2Lip inference invocation). Empty = avatar mode falls back
    # to b-roll. Kept as config because every lip-sync tool has a different CLI and
    # none of them install cleanly enough to hard-depend on.
    youtube_lipsync_cmd: str = ""

    # ── Telegram ───────────────────────────────────────────────────────
    telegram_bot_token: str = ""
    telegram_owner_chat_id: str = ""
    # Echoed by Telegram on every webhook call; this is what distinguishes a real
    # update from anyone who discovered the URL.
    telegram_webhook_secret: str = ""

    # ── Slack (phase 5.1) ──────────────────────────────────────────────
    slack_bot_token: str = ""
    # Every Slack request is signed with this. Without it the webhook cannot tell a real
    # event from anyone who found the URL, so an unset secret rejects rather than trusts.
    slack_signing_secret: str = ""

    # ── Canvas LMS (phase 5.3) ─────────────────────────────────────────
    # Per-institution host, e.g. https://canvas.instructure.com
    canvas_base_url: str = ""
    canvas_api_token: str = ""

    # ── WhatsApp Cloud API (phase 5.4) ─────────────────────────────────
    whatsapp_phone_number_id: str = ""
    whatsapp_access_token: str = ""
    # Echoed back during Meta's subscription handshake.
    whatsapp_verify_token: str = ""

    # ── Twilio voice escalation (phase 5.5) ────────────────────────────
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_from_number: str = ""
    # Minutes an approval may sit unanswered before Jarvis rings you and asks by voice
    # (PLAN.md 10.2.1). 0 never calls; the daily call cap and quiet hours still apply.
    twilio_call_for_approval_after_minutes: int = 5
    # A call is the loudest escalation and the only one that costs real money per use.
    # The cap is enforced in code, not in a spreadsheet (PLAN.md §14).
    max_calls_per_day: int = 3

    # ── OpenClaw adapter (phase 5.6) ───────────────────────────────────
    # The adapter runs with no database credentials; this shared secret is the only
    # thing it holds, and it buys nothing but the right to post an untrusted event.
    openclaw_shared_secret: str = ""

    # ── Alexa (phase 4) ────────────────────────────────────────────────
    # Requests naming any other skill id are rejected before they are parsed.
    alexa_skill_id: str = ""
    # Amazon's signature check is mandatory in production and impossible in a unit test
    # (it needs a live cert chain from Amazon). Off in local/test only; the code refuses
    # to skip it in any other environment.
    alexa_verify_signature: bool = True

    # ── push (FCM HTTP v1) ─────────────────────────────────────────────
    # Server side: a Firebase service-account JSON (path, or the JSON itself). Client
    # side: the four values from the Firebase console's Android app card — the phone
    # reads them from /v1/settings, so no google-services.json ships in the APK.
    fcm_credentials_path: str = Field(
        default="", description="Service-account JSON path (or the JSON)"
    )
    fcm_project_id: str = Field(
        default="", description="Firebase project id (blank = read from the JSON)"
    )
    fcm_api_key: str = Field(default="", description="Firebase Android API key (client)")
    fcm_app_id: str = Field(
        default="", description="Firebase Android app id 1:…:android:… (client)"
    )
    fcm_sender_id: str = Field(default="", description="Firebase messaging sender id (client)")

    # The always-on wake word on the phone is free and on-device (the platform
    # speech recogniser watches for "Jarvis"); the Mac node uses openWakeWord. No key.

    # ── device signing (blueprint §12) ─────────────────────────────────
    # PEM of the server's ECDSA private key. Generated per-process in local/test only;
    # a rotating key would silently invalidate every paired helper.
    device_signing_key_pem: str = ""
    device_job_ttl_seconds: int = 300

    # ── browser worker (cloud-side, so it works with the Mac offline) ──
    browser_headless: bool = True
    browser_timeout_seconds: int = 30

    # ── the assistant's character ──────────────────────────────────────
    # Appended to the persona: how to address you, what to care about, tone. The app's
    # Settings → Custom instructions. Never a place for facts — those go to memory.
    persona_extra: str = Field(default="", description="Custom instructions for Jarvis")
    # Which model answers chat by default: auto (fast free first) or a provider name.
    chat_provider: str = Field(
        default="auto",
        description="auto | groq | gemini | openrouter_free | openrouter_paid | ollama",
    )

    # ── voice ──────────────────────────────────────────────────────────
    # One neural voice for the phone, the Mac and the voice loop (edge-tts, keyless).
    # `GET /v1/tts/voices` lists a few worth trying; `edge-tts --list-voices` has all.
    tts_voice: str = "en-GB-RyanNeural"

    # ── artifacts (screenshots and files a device sends back) ──────────
    # ponytail: local disk. Swap for R2 when the VPS runs out of space — the route
    # streams by path, so the store is one function.
    artifact_dir: str = "var/artifacts"
    artifact_max_bytes: int = 50 * 1024 * 1024

    # ── kill switch ────────────────────────────────────────────────────
    global_pause: bool = Field(
        default=False,
        description="Rejects new R1-R3 actions and cancels queued jobs. Never deletes evidence.",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
