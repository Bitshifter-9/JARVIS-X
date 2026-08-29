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
    groq_model: str = "llama-3.3-70b-versatile"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    openrouter_api_key: str = ""
    openrouter_free_model: str = "deepseek/deepseek-chat-v3:free"
    openrouter_paid_model: str = "anthropic/claude-haiku-4.5"
    ollama_host: str = "http://127.0.0.1:11434"
    ollama_model: str = "llama3.1:8b"
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

    # ── queue ──────────────────────────────────────────────────────────
    job_visibility_timeout_seconds: int = 300
    job_max_attempts: int = 5
    job_backoff_base_seconds: float = 2.0
    job_backoff_cap_seconds: float = 600.0

    # ── provider health ────────────────────────────────────────────────
    provider_cooldown_seconds: int = 60
    provider_failure_threshold: int = 3

    # ── kill switch ────────────────────────────────────────────────────
    global_pause: bool = Field(
        default=False,
        description="Rejects new R1-R3 actions and cancels queued jobs. Never deletes evidence.",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
