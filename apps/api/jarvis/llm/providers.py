"""Providers, over LiteLLM.

LiteLLM handles transport, auth and provider quirks. This module supplies the mapping from
our call classes to concrete models, and translates provider errors into the taxonomy the
cascade branches on.

Cascade order, circuit breaking and budget enforcement stay in ``router.py``: LiteLLM's own
router is in-process, and our breaker state must be shared between the API and every worker.
"""

from __future__ import annotations

import time
from typing import Any

from jarvis.core.config import get_settings
from jarvis.llm.pricing import estimate_cost_inr
from jarvis.llm.types import (
    CallClass,
    LLMRequest,
    LLMResponse,
    ProviderNotConfigured,
    ProviderPermanentError,
    ProviderRateLimited,
    ProviderTransientError,
)


def _classify_error(provider: str, exc: Exception) -> Exception:
    import litellm

    if isinstance(exc, litellm.RateLimitError):
        return ProviderRateLimited(f"{provider} rate limited: {exc}")
    if isinstance(exc, litellm.AuthenticationError | litellm.PermissionDeniedError):
        return ProviderPermanentError(f"{provider} rejected credentials: {exc}")
    if isinstance(exc, litellm.BadRequestError | litellm.UnprocessableEntityError):
        return ProviderPermanentError(f"{provider} rejected the request: {exc}")
    if isinstance(exc, litellm.ContextWindowExceededError):
        return ProviderPermanentError(f"{provider} context window exceeded: {exc}")
    if isinstance(
        exc, litellm.Timeout | litellm.APIConnectionError | litellm.ServiceUnavailableError
    ):
        return ProviderTransientError(f"{provider} unavailable: {exc}")
    if isinstance(exc, litellm.InternalServerError):
        return ProviderTransientError(f"{provider} server error: {exc}")
    return ProviderTransientError(f"{provider} failed: {type(exc).__name__}: {exc}")


class LiteLLMProvider:
    """One configured model, reachable through LiteLLM."""

    name = "litellm"
    is_paid = False
    model = ""
    api_key = ""
    supports_json_schema = True
    extra_params: dict[str, Any] = {}

    def is_configured(self) -> bool:
        return bool(self.model and self.api_key)

    def supports(self, call_class: CallClass) -> bool:
        return call_class is not CallClass.EXTRACT or self.supports_json_schema

    async def generate(self, request: LLMRequest) -> LLMResponse:
        if not self.is_configured():
            raise ProviderNotConfigured(f"{self.name} has no API key configured")

        import litellm

        params: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in request.messages],
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
            "api_key": self.api_key,
            "num_retries": 0,
            "timeout": 60,
            **self.extra_params,
        }
        if request.stop:
            params["stop"] = request.stop
        if request.json_schema:
            params["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "extraction",
                    "strict": True,
                    "schema": request.json_schema,
                },
            }
        if request.samples > 1:
            params["n"] = request.samples

        started = time.perf_counter()
        try:
            response = await litellm.acompletion(**params)
        except Exception as exc:
            raise _classify_error(self.name, exc) from exc

        latency_ms = int((time.perf_counter() - started) * 1000)
        choices = [c.message.content or "" for c in response.choices]
        usage = response.usage
        inp = int(getattr(usage, "prompt_tokens", 0) or 0)
        out = int(getattr(usage, "completion_tokens", 0) or 0)

        return LLMResponse(
            text=choices[0],
            samples=choices,
            provider=self.name,
            model=self.model,
            input_tokens=inp,
            output_tokens=out,
            latency_ms=latency_ms,
            cost_inr=estimate_cost_inr(self.name, self.model, inp, out, is_paid=self.is_paid),
        )


class GroqProvider(LiteLLMProvider):
    name = "groq"

    def __init__(self) -> None:
        s = get_settings()
        self.api_key = s.groq_api_key
        self.model = f"groq/{s.groq_model}"


class GeminiProvider(LiteLLMProvider):
    name = "gemini"

    def __init__(self) -> None:
        s = get_settings()
        self.api_key = s.gemini_api_key
        self.model = f"gemini/{s.gemini_model}"


class OpenRouterFreeProvider(LiteLLMProvider):
    name = "openrouter_free"

    def __init__(self) -> None:
        s = get_settings()
        self.api_key = s.openrouter_api_key
        self.model = f"openrouter/{s.openrouter_free_model}"


class OpenRouterPaidProvider(LiteLLMProvider):
    name = "openrouter_paid"
    is_paid = True

    def __init__(self) -> None:
        s = get_settings()
        self.api_key = s.openrouter_api_key
        self.model = f"openrouter/{s.openrouter_paid_model}"


class OllamaProvider(LiteLLMProvider):
    name = "ollama"
    supports_json_schema = False

    def __init__(self) -> None:
        s = get_settings()
        self.model = f"ollama/{s.ollama_model}"
        self.api_key = "ollama"
        self.extra_params = {"api_base": s.ollama_host}

    def is_configured(self) -> bool:
        return bool(self.model)
