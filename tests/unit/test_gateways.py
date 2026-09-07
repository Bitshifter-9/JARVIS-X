"""The LLM gateways widen the cascade (FEATURES-50: LLM gateways)."""

from jarvis.llm.router import DEFAULT_CASCADE, default_providers
from jarvis.llm.types import CallClass


def test_the_new_gateways_are_registered():
    providers = default_providers()
    for name in ("gateway", "cerebras", "openrouter_free2", "groq", "gemini", "ollama"):
        assert name in providers


def test_the_custom_gateway_is_first_and_off_until_configured(monkeypatch):
    from jarvis.core.config import get_settings

    # Off by default (no base url) → not configured, so the cascade skips it.
    assert not default_providers()["gateway"].is_configured()

    monkeypatch.setattr(get_settings(), "gateway_base_url", "https://proxy.test/v1")
    monkeypatch.setattr(get_settings(), "gateway_api_key", "sk-x")
    gw = default_providers()["gateway"]
    assert gw.is_configured()
    assert gw.extra_params["api_base"] == "https://proxy.test/v1"
    # It leads every cascade, so one gateway key can serve everything.
    for order in DEFAULT_CASCADE.values():
        assert order[0] == "gateway"


def test_cerebras_and_the_second_openrouter_are_in_the_free_lane():
    for order in DEFAULT_CASCADE.values():
        assert "cerebras" in order
    assert "openrouter_free2" in DEFAULT_CASCADE[CallClass.CHAT]
