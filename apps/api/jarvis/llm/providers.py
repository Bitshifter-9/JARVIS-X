"""Concrete providers: Groq, Gemini, OpenRouter (free and paid), Ollama."""

from __future__ import annotations

import time
from typing import Any

import httpx

from jarvis.core.config import get_settings
from jarvis.llm.base import (
    DEFAULT_TIMEOUT,
    OpenAICompatibleProvider,
    _maybe_json,
    _raise_for_status,
)
from jarvis.llm.pricing import estimate_cost_inr
from jarvis.llm.types import (
    CallClass,
    LLMRequest,
    LLMResponse,
    ProviderNotConfigured,
    ProviderPermanentError,
    ProviderTransientError,
)


class GroqProvider(OpenAICompatibleProvider):
    """Primary for classify / plan / reflect / chat.

    Chosen for latency: on stage, a reply that arrives in 300 ms feels like a system and
    one that arrives in 4 s feels like a demo.
    """

    name = "groq"
    is_paid = False
    base_url = "https://api.groq.com/openai/v1"

    def __init__(self) -> None:
        s = get_settings()
        self.api_key = s.groq_api_key
        self.model = s.groq_model


class OpenRouterFreeProvider(OpenAICompatibleProvider):
    """Overflow on ``:free`` model variants — heavily rate limited, but costs nothing."""

    name = "openrouter_free"
    is_paid = False
    base_url = "https://openrouter.ai/api/v1"

    def __init__(self) -> None:
        s = get_settings()
        self.api_key = s.openrouter_api_key
        self.model = s.openrouter_free_model

    def _headers(self) -> dict[str, str]:
        return super()._headers() | {
            "HTTP-Referer": "https://github.com/Bitshifter-9/JARVIS-X",
            "X-Title": "JARVIS X",
        }


class OpenRouterPaidProvider(OpenRouterFreeProvider):
    """Last resort. Only reachable when paid inference is enabled *and* budget remains."""

    name = "openrouter_paid"
    is_paid = True

    def __init__(self) -> None:
        s = get_settings()
        self.api_key = s.openrouter_api_key
        self.model = s.openrouter_paid_model


class OllamaProvider(OpenAICompatibleProvider):
    """Local Ollama. Optional and never required — the Mac may be offline (PLAN.md §3)."""

    name = "ollama"
    is_paid = False
    supports_json_schema = False

    def __init__(self) -> None:
        s = get_settings()
        self.base_url = f"{s.ollama_host.rstrip('/')}/v1"
        self.api_key = "ollama"  # Ollama ignores it, but the shared client expects one
        self.model = s.ollama_model

    def is_configured(self) -> bool:
        return bool(self.model)


class GeminiProvider:
    """Primary for EXTRACT.

    Gemini's ``responseSchema`` constrains decoding to the schema rather than merely
    asking for it in a prompt, which is what a 90% extraction target needs.
    """

    name = "gemini"
    is_paid = False
    base_url = "https://generativelanguage.googleapis.com/v1beta"

    def __init__(self) -> None:
        s = get_settings()
        self.api_key = s.gemini_api_key
        self.model = s.gemini_model

    def is_configured(self) -> bool:
        return bool(self.api_key and self.model)

    def supports(self, call_class: CallClass) -> bool:  # noqa: ARG002
        return True

    async def generate(self, request: LLMRequest) -> LLMResponse:
        if not self.is_configured():
            raise ProviderNotConfigured("gemini has no API key configured")

        system = "\n\n".join(m.content for m in request.messages if m.role == "system")
        contents = [
            {"role": "model" if m.role == "assistant" else "user", "parts": [{"text": m.content}]}
            for m in request.messages
            if m.role != "system"
        ]

        body: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "maxOutputTokens": request.max_tokens,
                "temperature": request.temperature,
            },
        }
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        if request.json_schema:
            body["generationConfig"]["responseMimeType"] = "application/json"
            body["generationConfig"]["responseSchema"] = _to_gemini_schema(request.json_schema)

        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
                response = await client.post(
                    f"{self.base_url}/models/{self.model}:generateContent",
                    headers={"x-goog-api-key": self.api_key, "Content-Type": "application/json"},
                    json=body,
                )
        except httpx.TimeoutException as exc:
            raise ProviderTransientError(f"gemini timed out: {exc}") from exc
        except httpx.HTTPError as exc:
            raise ProviderTransientError(f"gemini transport error: {exc}") from exc

        _raise_for_status(self.name, response)
        data = response.json()
        latency_ms = int((time.perf_counter() - started) * 1000)

        try:
            parts = data["candidates"][0]["content"]["parts"]
            text = "".join(p.get("text", "") for p in parts)
        except (KeyError, IndexError, TypeError) as exc:
            # A blocked prompt returns no candidates. Permanent for this request.
            reason = (data.get("promptFeedback") or {}).get("blockReason", "no candidates")
            raise ProviderPermanentError(f"gemini returned no content: {reason}") from exc

        usage = data.get("usageMetadata") or {}
        inp = int(usage.get("promptTokenCount", 0))
        out = int(usage.get("candidatesTokenCount", 0))

        return LLMResponse(
            text=text,
            provider=self.name,
            model=self.model,
            input_tokens=inp,
            output_tokens=out,
            latency_ms=latency_ms,
            cost_inr=estimate_cost_inr(self.name, self.model, inp, out, is_paid=self.is_paid),
            parsed=_maybe_json(text) if request.json_schema else None,
        )


def _to_gemini_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Strip JSON Schema keywords Gemini's OpenAPI subset rejects.

    Passing ``$schema`` or ``additionalProperties`` through produces a 400, which would
    burn the primary extraction provider on every single call.
    """
    unsupported = {"$schema", "$id", "$defs", "additionalProperties", "definitions", "title"}
    if not isinstance(schema, dict):
        return schema
    cleaned = {k: v for k, v in schema.items() if k not in unsupported}
    if "properties" in cleaned and isinstance(cleaned["properties"], dict):
        cleaned["properties"] = {
            k: _to_gemini_schema(v) for k, v in cleaned["properties"].items()
        }
    if "items" in cleaned:
        cleaned["items"] = _to_gemini_schema(cleaned["items"])
    return cleaned
