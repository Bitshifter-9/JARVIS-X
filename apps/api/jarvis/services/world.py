"""The world outside the inbox (PLAN.md 10.9.3): weather and headlines, keyless.

Open-Meteo needs no key; headlines reuse the video pipeline's news research. Both are
R0 reads whose results are data, so the morning routine can cite live values.
"""

from __future__ import annotations

from typing import Any

import httpx
from jarvis.core.logging import get_logger

log = get_logger(__name__)

GEOCODE = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST = "https://api.open-meteo.com/v1/forecast"
CODES = {
    0: "clear", 1: "mostly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "fog", 51: "light drizzle", 53: "drizzle", 55: "heavy drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain", 71: "snow", 73: "snow", 75: "heavy snow",
    80: "showers", 81: "showers", 82: "heavy showers", 95: "thunderstorm",
    96: "thunderstorm with hail", 99: "thunderstorm with hail",
}


async def weather(location: str, *, transport=None) -> dict[str, Any]:  # noqa: ANN001
    """Current conditions plus today's range and rain chance for a place name."""
    if not location.strip():
        return {"error": "no location — set owner_location in Settings"}
    async with httpx.AsyncClient(timeout=15, transport=transport) as client:
        geo = (
            await client.get(GEOCODE, params={"name": location, "count": 1, "language": "en"})
        ).json()
        places = geo.get("results") or []
        if not places:
            return {"error": f"could not find {location!r}"}
        place = places[0]
        data = (
            await client.get(
                FORECAST,
                params={
                    "latitude": place["latitude"],
                    "longitude": place["longitude"],
                    "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m",
                    "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                    "timezone": "auto",
                    "forecast_days": 1,
                },
            )
        ).json()
    current, daily = data.get("current", {}), data.get("daily", {})
    code = int(current.get("weather_code") or 0)
    return {
        "place": f"{place.get('name')}, {place.get('country', '')}".strip(", "),
        "summary": CODES.get(code, "unknown"),
        "temperature_c": current.get("temperature_2m"),
        "feels_like_c": current.get("apparent_temperature"),
        "wind_kmh": current.get("wind_speed_10m"),
        "high_c": (daily.get("temperature_2m_max") or [None])[0],
        "low_c": (daily.get("temperature_2m_min") or [None])[0],
        "rain_chance_pct": (daily.get("precipitation_probability_max") or [None])[0],
    }


async def headlines(topic: str, *, max_results: int = 6) -> list[str]:
    from jarvis.services.youtube.pipeline import research

    return await research(topic or "top news today", max_results=max_results, is_news=True)
