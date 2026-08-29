"""Provider interface, plus a shared OpenAI-compatible implementation.

Groq and OpenRouter both speak the OpenAI chat-completions shape, so they differ only in
base URL, model and headers. Gemini does not, and gets its own module.
"""

from __future__ import annotations

import json
import time
from typing import Any, Protocol, runtime_checkable

import httpx

from jarvis.core.logging import get_logger
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

log = get_logger(__name__)

DEFAULT_TIMEOUT = httpx.Timeout(connect=5.0, read=60.0, write=10.0, pool=5.0)


@runtime_checkable
class LLMProvider(Protocol):
    name: str
    is_paid: bool

    def is_configured(self) -> bool: ...
    def supports(self, call_class: CallClass) -> bool: ...
    async def generate(self, request: LLMRequest) -> LLMResponse: ...


class OpenAICompatibleProvider:
    """Shared implementation for any endpoint speaking OpenAI chat-completions."""

    name = "openai-compatible"
    is_paid = False
    base_url = ""
    api_key = ""
    model = ""
    supports_json_schema = True

    def is_configured(self) -> bool:
        return bool(self.api_key and self.model)

    def supports(self, call_class: CallClass) -> bool:  # noqa: ARG002
        return True

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def _body(self, request: LLMRequest) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in request.messages],
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
        }
        if request.stop:
            body["stop"] = request.stop
        if request.json_schema and self.supports_json_schema:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "extraction",
                    "strict": True,
                    "schema": request.json_schema,
                },
            }
        return body

    async def generate(self, request: LLMRequest) -> LLMResponse:
        if not self.is_configured():
            raise ProviderNotConfigured(f"{self.name} has no API key configured")

        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers=self._headers(),
                    json=self._body(request),
                )
        except httpx.TimeoutException as exc:
            raise ProviderTransientError(f"{self.name} timed out: {exc}") from exc
        except httpx.HTTPError as exc:
            raise ProviderTransientError(f"{self.name} transport error: {exc}") from exc

        _raise_for_status(self.name, response)

        latency_ms = int((time.perf_counter() - started) * 1000)
        return self._parse(response.json(), request, latency_ms)

    def _parse(self, data: dict, request: LLMRequest, latency_ms: int) -> LLMResponse:
        try:
            text = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderPermanentError(f"{self.name} returned an unreadable body") from exc

        usage = data.get("usage") or {}
        inp = int(usage.get("prompt_tokens", 0))
        out = int(usage.get("completion_tokens", 0))

        return LLMResponse(
            text=text,
            provider=self.name,
            model=data.get("model", self.model),
            input_tokens=inp,
            output_tokens=out,
            latency_ms=latency_ms,
            cost_inr=estimate_cost_inr(self.name, self.model, inp, out, is_paid=self.is_paid),
            parsed=_maybe_json(text) if request.json_schema else None,
        )


def _raise_for_status(provider: str, response: httpx.Response) -> None:
    """Translate HTTP status into the taxonomy the cascade branches on."""
    if response.status_code < 400:
        return

    body = response.text[:300]
    if response.status_code == 429:
        retry_after = response.headers.get("retry-after")
        raise ProviderRateLimited(
            f"{provider} rate limited: {body}",
            retry_after=float(retry_after) if retry_after and retry_after.isdigit() else None,
        )
    if response.status_code in (401, 403):
        # Not transient: a bad key will still be bad on the next attempt.
        raise ProviderPermanentError(f"{provider} rejected credentials ({response.status_code})")
    if response.status_code >= 500:
        raise ProviderTransientError(f"{provider} server error {response.status_code}: {body}")
    raise ProviderPermanentError(f"{provider} rejected request {response.status_code}: {body}")


def _maybe_json(text: str) -> dict | None:
    """Best-effort JSON parse.

    Some models wrap JSON in a markdown fence despite being asked not to. Recovering from
    that is cheaper than failing the extraction and burning another provider's quota.
    """
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = candidate.split("```")[1] if "```" in candidate[3:] else candidate[3:]
        candidate = candidate.removeprefix("json").strip()
    try:
        parsed = json.loads(candidate)
    except (json.JSONDecodeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None
