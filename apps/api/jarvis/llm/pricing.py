"""Token pricing, in INR per million tokens.

Free tiers are genuinely zero, so a free-tier call contributes nothing to the budget and
the guard never blocks it. Paid entries are approximate and exist to *bound* spend, not to
reconcile an invoice — they are deliberately rounded up.
"""

from __future__ import annotations

from dataclasses import dataclass

USD_TO_INR = 90.0  # rounded up; a conservative estimate over-reports spend, which is safe


@dataclass(frozen=True)
class Price:
    input_inr_per_mtok: float
    output_inr_per_mtok: float

    def cost_inr(self, input_tokens: int, output_tokens: int) -> float:
        return (
            input_tokens * self.input_inr_per_mtok + output_tokens * self.output_inr_per_mtok
        ) / 1_000_000


FREE = Price(0.0, 0.0)

# Keyed by "provider:model". Unknown models fall back to FREE_TIER_DEFAULT so a
# misconfiguration cannot silently invent spend — but a paid provider always
# falls back to PAID_UNKNOWN, so it cannot silently hide it either.
PRICES: dict[str, Price] = {
    # Groq free tier
    "groq:llama-3.3-70b-versatile": FREE,
    "groq:llama-3.1-8b-instant": FREE,
    # Gemini free tier
    "gemini:gemini-2.5-flash": FREE,
    "gemini:gemini-2.5-flash-lite": FREE,
    # OpenRouter ":free" variants
    "openrouter:deepseek/deepseek-chat-v3:free": FREE,
    "openrouter:meta-llama/llama-3.3-70b-instruct:free": FREE,
    # OpenRouter paid
    "openrouter:anthropic/claude-haiku-4.5": Price(1.0 * USD_TO_INR, 5.0 * USD_TO_INR),
    "openrouter:deepseek/deepseek-chat-v3": Price(0.28 * USD_TO_INR, 0.88 * USD_TO_INR),
    # Local
    "ollama:*": FREE,
}

FREE_TIER_DEFAULT = FREE
PAID_UNKNOWN = Price(2.0 * USD_TO_INR, 8.0 * USD_TO_INR)  # pessimistic on purpose


def price_for(provider: str, model: str, *, is_paid: bool) -> Price:
    if (exact := PRICES.get(f"{provider}:{model}")) is not None:
        return exact
    if (wildcard := PRICES.get(f"{provider}:*")) is not None:
        return wildcard
    return PAID_UNKNOWN if is_paid else FREE_TIER_DEFAULT


def estimate_cost_inr(provider: str, model: str, inp: int, out: int, *, is_paid: bool) -> float:
    return price_for(provider, model, is_paid=is_paid).cost_inr(inp, out)
