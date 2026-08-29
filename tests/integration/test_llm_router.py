"""Phase 0.6 gate: the LLM router.

Exit test from PLAN.md §12: *kill the primary provider mid-run — the run completes on the
next tier.* Plus the budget invariant from §14: with paid inference disabled the system
must still work end to end on free providers alone.

Fake providers throughout. A test that needs a live API key is a test that does not run.
"""

from __future__ import annotations

import pytest
from jarvis.core.config import get_settings
from jarvis.llm.router import LLMRouter
from jarvis.llm.types import (
    AllProvidersFailed,
    CallClass,
    LLMRequest,
    LLMResponse,
    Message,
    ProviderNotConfigured,
    ProviderPermanentError,
    ProviderRateLimited,
    ProviderTransientError,
)
from sqlalchemy import text


class FakeProvider:
    """A provider whose behaviour the test dictates."""

    def __init__(
        self,
        name: str,
        *,
        is_paid: bool = False,
        configured: bool = True,
        fail_with: Exception | None = None,
        cost_inr: float = 0.0,
        supports_classes: set[CallClass] | None = None,
    ) -> None:
        self.name = name
        self.is_paid = is_paid
        self.model = f"{name}-model"
        self._configured = configured
        self.fail_with = fail_with
        self.cost_inr = cost_inr
        self._supports = supports_classes
        self.calls = 0

    def is_configured(self) -> bool:
        return self._configured

    def supports(self, call_class: CallClass) -> bool:
        return self._supports is None or call_class in self._supports

    async def generate(self, request: LLMRequest) -> LLMResponse:
        self.calls += 1
        if self.fail_with is not None:
            raise self.fail_with
        return LLMResponse(
            text=f"answer from {self.name}",
            provider=self.name,
            model=self.model,
            input_tokens=100,
            output_tokens=50,
            latency_ms=42,
            cost_inr=self.cost_inr,
        )


def _req(call_class: CallClass = CallClass.CHAT) -> LLMRequest:
    return LLMRequest(
        call_class=call_class, messages=[Message("user", "what is my next deadline?")]
    )


def _router(session, providers, cascade_names: tuple[str, ...]) -> LLMRouter:
    return LLMRouter(
        session,
        providers={p.name: p for p in providers},
        cascade=dict.fromkeys(CallClass, cascade_names),
    )


# ── the gate ───────────────────────────────────────────────────────────
async def test_cascade_advances_when_primary_is_rate_limited(session):
    """A 429 on the primary must not fail the run — the second tier answers."""
    primary = FakeProvider("groq", fail_with=ProviderRateLimited("429 quota exceeded"))
    secondary = FakeProvider("gemini")

    router = _router(session, [primary, secondary], ("groq", "gemini"))
    response = await router.generate(_req())
    await session.commit()

    assert response.provider == "gemini"
    assert response.attempts == 2, "the fallback knows it was the second attempt"
    assert primary.calls == 1 and secondary.calls == 1

    # Both outcomes are accounted for, not just the successful one.
    rows = (
        await session.execute(
            text("SELECT provider, status FROM llm_calls ORDER BY created_at, provider")
        )
    ).mappings().all()
    assert {(r["provider"], r["status"]) for r in rows} == {("groq", "error"), ("gemini", "ok")}


async def test_cascade_survives_every_transient_failure_mode(session):
    """Timeouts, 5xx and permanent rejections all advance rather than abort."""
    providers = [
        FakeProvider("groq", fail_with=ProviderTransientError("connection reset")),
        FakeProvider("gemini", fail_with=ProviderPermanentError("400 bad schema")),
        FakeProvider("openrouter_free"),
    ]
    router = _router(session, providers, ("groq", "gemini", "openrouter_free"))
    response = await router.generate(_req())
    await session.commit()

    assert response.provider == "openrouter_free"
    assert response.attempts == 3


async def test_all_providers_failed_reports_why_each_one_did(session):
    providers = [
        FakeProvider("groq", fail_with=ProviderRateLimited("429")),
        FakeProvider("gemini", configured=False),
    ]
    router = _router(session, providers, ("groq", "gemini"))

    with pytest.raises(AllProvidersFailed) as exc:
        await router.generate(_req())
    await session.commit()

    assert set(exc.value.failures) == {"groq", "gemini"}
    assert "429" in exc.value.failures["groq"]
    assert "not configured" in exc.value.failures["gemini"]


# ── the budget invariant (PLAN.md §14) ─────────────────────────────────
async def test_paid_provider_is_unreachable_when_paid_llm_disabled(session):
    """ENABLE_PAID_LLM=false must still yield a working system on free tiers alone."""
    assert get_settings().enable_paid_llm is False, "the safe default must be off"

    free = FakeProvider("openrouter_free")
    paid = FakeProvider("openrouter_paid", is_paid=True, cost_inr=5.0)

    router = _router(session, [free, paid], ("openrouter_free", "openrouter_paid"))
    response = await router.generate(_req())
    await session.commit()

    assert response.provider == "openrouter_free"
    assert paid.calls == 0, "a paid provider must never be dialled while paid inference is off"


async def test_paid_provider_skipped_when_free_tier_fails_and_paid_disabled(session):
    """The important half of the invariant: disabled means *unreachable*, not *last resort*."""
    free = FakeProvider("openrouter_free", fail_with=ProviderRateLimited("429"))
    paid = FakeProvider("openrouter_paid", is_paid=True, cost_inr=5.0)

    router = _router(session, [free, paid], ("openrouter_free", "openrouter_paid"))
    with pytest.raises(AllProvidersFailed) as exc:
        await router.generate(_req())
    await session.commit()

    assert paid.calls == 0
    assert "paid inference disabled" in exc.value.failures["openrouter_paid"]


async def test_budget_cap_blocks_paid_provider_once_spend_reaches_limit(session, monkeypatch):
    """Spend past the cap removes the paid tier; free providers keep working."""
    settings = get_settings()
    monkeypatch.setattr(settings, "enable_paid_llm", True)
    monkeypatch.setattr(settings, "llm_budget_inr", 10.0)

    paid = FakeProvider("openrouter_paid", is_paid=True, cost_inr=4.0)
    router = _router(session, [paid], ("openrouter_paid",))

    # Under the cap: three calls at ₹4 reach ₹12, so the first three succeed...
    for _ in range(3):
        await router.generate(_req())
        await session.commit()
    assert paid.calls == 3

    # ...and the fourth is refused, because month-to-date spend now exceeds ₹10.
    with pytest.raises(AllProvidersFailed) as exc:
        await router.generate(_req())
    await session.commit()
    assert "budget exhausted" in exc.value.failures["openrouter_paid"]
    assert paid.calls == 3, "no request may be dispatched after the cap is reached"


async def test_free_providers_unaffected_by_an_exhausted_budget(session, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "enable_paid_llm", True)
    monkeypatch.setattr(settings, "llm_budget_inr", 0.0)

    free = FakeProvider("groq")
    paid = FakeProvider("openrouter_paid", is_paid=True, cost_inr=9.0)
    router = _router(session, [paid, free], ("openrouter_paid", "groq"))

    response = await router.generate(_req())
    await session.commit()
    assert response.provider == "groq"
    assert paid.calls == 0


# ── circuit breaker ────────────────────────────────────────────────────
async def test_breaker_opens_after_threshold_and_stops_dialling_the_provider(session, monkeypatch):
    """A rate-limited provider's remaining quota is precious; stop spending it on failures."""
    settings = get_settings()
    monkeypatch.setattr(settings, "provider_failure_threshold", 3)
    monkeypatch.setattr(settings, "provider_cooldown_seconds", 300)

    flaky = FakeProvider("groq", fail_with=ProviderRateLimited("429"))
    healthy = FakeProvider("gemini")
    router = _router(session, [flaky, healthy], ("groq", "gemini"))

    for _ in range(3):
        await router.generate(_req())
        await session.commit()
    assert flaky.calls == 3

    # Breaker is now open: the next run skips it without a network call.
    response = await router.generate(_req())
    await session.commit()
    assert response.provider == "gemini"
    assert flaky.calls == 3, "an open circuit must not be dialled"

    cooling = await router.health.cooling_down()
    assert "groq" in cooling


async def test_success_closes_the_breaker(session, monkeypatch):
    monkeypatch.setattr(get_settings(), "provider_failure_threshold", 2)

    provider = FakeProvider("groq", fail_with=ProviderTransientError("blip"))
    router = _router(session, [provider], ("groq",))

    with pytest.raises(AllProvidersFailed):
        await router.generate(_req())
    await session.commit()

    provider.fail_with = None
    await router.generate(_req())
    await session.commit()

    row = (
        await session.execute(
            text("SELECT consecutive_failures, cooldown_until FROM provider_health "
                 "WHERE provider='groq'")
        )
    ).mappings().one()
    assert row["consecutive_failures"] == 0
    assert row["cooldown_until"] is None


# ── routing by intent ──────────────────────────────────────────────────
async def test_extract_prefers_the_schema_capable_provider(session):
    """EXTRACT routes for accuracy, CHAT routes for latency — from the same registry."""
    groq = FakeProvider("groq")
    gemini = FakeProvider("gemini")
    router = LLMRouter(
        session,
        providers={"groq": groq, "gemini": gemini},
        cascade={CallClass.EXTRACT: ("gemini", "groq"), CallClass.CHAT: ("groq", "gemini")},
    )

    extraction = await router.generate(_req(CallClass.EXTRACT))
    chat = await router.generate(_req(CallClass.CHAT))
    await session.commit()

    assert extraction.provider == "gemini"
    assert chat.provider == "groq"


async def test_provider_not_configured_is_skipped_not_counted_as_a_failure(session):
    """An absent API key is an absent provider, not a broken one."""

    class Unconfigured(FakeProvider):
        async def generate(self, request):
            raise ProviderNotConfigured("no key")

    absent = Unconfigured("gemini")
    working = FakeProvider("groq")
    router = _router(session, [absent, working], ("gemini", "groq"))

    await router.generate(_req())
    await session.commit()

    health = (
        await session.execute(text("SELECT provider FROM provider_health"))
    ).scalars().all()
    assert "gemini" not in health, "a missing key must not trip a circuit breaker"


async def test_every_call_is_accounted_for(session):
    """Unrecorded spend is spend nobody can see (PLAN.md §14)."""
    provider = FakeProvider("groq", cost_inr=0.0)
    router = _router(session, [provider], ("groq",))

    await router.generate(
        LLMRequest(
            call_class=CallClass.PLAN,
            messages=[Message("user", "plan it")],
            prompt_version="plan/v3",
        )
    )
    await session.commit()

    row = (
        await session.execute(
            text("SELECT call_class, provider, model, prompt_version, input_tokens, "
                 "output_tokens, latency_ms, status FROM llm_calls")
        )
    ).mappings().one()
    assert row["call_class"] == "plan"
    assert row["provider"] == "groq"
    assert row["prompt_version"] == "plan/v3"
    assert row["input_tokens"] == 100
    assert row["output_tokens"] == 50
    assert row["status"] == "ok"
