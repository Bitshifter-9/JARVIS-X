"""LLM request/response contracts and the failure taxonomy the cascade branches on."""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass, field
from typing import Any


class CallClass(enum.StrEnum):
    """What a call is *for*. The router picks a cascade from this, not from a model name.

    Separating intent from model means swapping providers is a config change, and means
    ``extract`` can be routed for accuracy while ``chat`` is routed for latency.
    """

    CLASSIFY = "classify"
    PLAN = "plan"
    EXTRACT = "extract"
    CHAT = "chat"
    REFLECT = "reflect"


@dataclass(frozen=True)
class Message:
    role: str  # system | user | assistant
    content: str


@dataclass
class LLMRequest:
    call_class: CallClass
    messages: list[Message]
    max_tokens: int = 1024
    temperature: float = 0.2
    # Present for EXTRACT. Providers that support native structured output enforce it;
    # the router validates the result regardless, because a schema the model merely
    # saw is not a schema the model obeyed.
    json_schema: dict[str, Any] | None = None
    prompt_version: str | None = None
    user_id: uuid.UUID | None = None
    stop: list[str] = field(default_factory=list)


@dataclass
class LLMResponse:
    text: str
    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    cost_inr: float = 0.0
    parsed: dict[str, Any] | None = None
    attempts: int = 1


# ── failure taxonomy ───────────────────────────────────────────────────
# The distinction that matters: does advancing to the next provider stand a chance?
class LLMError(Exception):
    """Base class."""


class ProviderNotConfigured(LLMError):
    """No API key. Skipped silently — an unconfigured provider is not a failure."""


class ProviderTransientError(LLMError):
    """Rate limited, timed out, or 5xx. Another provider may well succeed: advance."""

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class ProviderRateLimited(ProviderTransientError):
    """Explicit 429. Same handling as transient, but counted separately for health."""


class ProviderPermanentError(LLMError):
    """A malformed request or a rejected schema. Retrying elsewhere usually repeats it,
    but we still advance — providers disagree about what they accept."""


class BudgetExhausted(LLMError):
    """The paid tier is barred by the budget guard. Free providers remain available."""


class AllProvidersFailed(LLMError):
    """Every provider in the cascade was skipped or failed. Carries the reason for each,
    so the log says *why* the system could not think rather than merely that it could not."""

    def __init__(self, call_class: CallClass, failures: dict[str, str]) -> None:
        self.call_class = call_class
        self.failures = failures
        detail = "; ".join(f"{k}: {v}" for k, v in failures.items()) or "no providers configured"
        super().__init__(f"all providers failed for {call_class.value} — {detail}")
