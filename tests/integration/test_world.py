"""Weather and headlines (PLAN.md 10.9.3), keyless, as R0 reads."""

from __future__ import annotations

import json

import httpx
from jarvis.core.config import get_settings
from jarvis.services.agent.executor import ToolExecutor
from jarvis.services.identity import IdentityService
from jarvis.services.tool_gateway import ToolGateway
from jarvis.services.world import weather


def fake_open_meteo(request: httpx.Request) -> httpx.Response:
    if "geocoding" in request.url.host:
        if request.url.params["name"] == "Nowhere":
            return httpx.Response(200, json={"results": []})
        place = {"name": "Hyderabad", "country": "India", "latitude": 17.4, "longitude": 78.5}
        return httpx.Response(200, json={"results": [place]})
    return httpx.Response(
        200,
        json={
            "current": {
                "temperature_2m": 29.1,
                "apparent_temperature": 32.0,
                "weather_code": 61,
                "wind_speed_10m": 12.0,
            },
            "daily": {
                "temperature_2m_max": [31.0],
                "temperature_2m_min": [23.5],
                "precipitation_probability_max": [70],
            },
        },
    )


async def test_weather_reads_open_meteo():
    transport = httpx.MockTransport(fake_open_meteo)
    result = await weather("Hyderabad", transport=transport)
    assert result == {
        "place": "Hyderabad, India",
        "summary": "light rain",
        "temperature_c": 29.1,
        "feels_like_c": 32.0,
        "wind_kmh": 12.0,
        "high_c": 31.0,
        "low_c": 23.5,
        "rain_chance_pct": 70,
    }
    assert "error" in await weather("Nowhere", transport=transport)
    assert "error" in await weather("", transport=transport)


async def test_the_tools_are_r0_and_use_the_owners_location(session, monkeypatch):
    user = await IdentityService(session).register("world@example.com", "correct-horse-battery")
    monkeypatch.setattr(get_settings(), "owner_location", "Hyderabad")

    async def fake_weather(location, *, transport=None):  # noqa: ANN001
        return {"place": location, "summary": "clear", "temperature_c": 25}

    async def fake_headlines(topic, *, max_results=6):  # noqa: ANN001
        return [f"{topic}: headline one", "headline two"]

    monkeypatch.setattr("jarvis.services.world.weather", fake_weather)
    monkeypatch.setattr("jarvis.services.world.headlines", fake_headlines)

    gateway = ToolGateway(session)
    w = await gateway.propose(user.id, tool="weather.now", args={})
    assert w.policy.risk.value == "R0" and not w.needs_approval
    observed = await ToolExecutor(session).run(await gateway.authorize_dispatch(w.action.id))
    assert observed["place"] == "Hyderabad" and observed["status"] == 200

    n = await gateway.propose(user.id, tool="news.headlines", args={"topic": "ISRO"})
    observed = await ToolExecutor(session).run(await gateway.authorize_dispatch(n.action.id))
    assert observed["untrusted_headlines"][0].startswith("ISRO")
    assert json.dumps(observed)  # serialisable, like every observation
