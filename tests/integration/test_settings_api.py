"""The settings surface: allowlisted fields only, visible to their owner.

Keys are returned to the authenticated owner (single-tenant, their own server) so
the apps can prefill fields — the R4 credential rule guards the agent, not the UI.
"""

from __future__ import annotations

import pytest
from jarvis.api.routes import settings as settings_routes
from jarvis.core.config import get_settings

PASSWORD = "correct-horse-battery-staple"  # noqa: S105


@pytest.fixture
async def auth(client):
    await client.post("/v1/auth/register", json={"email": "cfg@example.com", "password": PASSWORD})
    tokens = (
        await client.post("/v1/auth/login", json={"email": "cfg@example.com", "password": PASSWORD})
    ).json()
    return {"Authorization": f"Bearer {tokens['access_token']}"}


@pytest.fixture
def env_file(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    monkeypatch.setattr(settings_routes, "ENV_PATH", path)
    s = get_settings()
    saved = {
        name: getattr(s, name)
        for name in settings_routes.SECRET_FIELDS + settings_routes.PLAIN_FIELDS
    }
    yield path
    for name, value in saved.items():
        setattr(s, name, value)


async def test_secrets_round_trip_and_persist(client, auth, env_file):
    # No emptiness assumption: the owner may have real keys in .env already.
    updated = (
        await client.put(
            "/v1/settings",
            json={"pexels_api_key": "px-123", "youtube_daily_topic": "AI news"},
            headers=auth,
        )
    ).json()
    assert updated["secrets"]["pexels_api_key"] is True
    assert updated["secret_values"]["pexels_api_key"] == "px-123"
    assert updated["values"]["youtube_daily_topic"] == "AI news"

    content = env_file.read_text()
    assert "JARVIS_PEXELS_API_KEY=px-123" in content
    assert "JARVIS_YOUTUBE_DAILY_TOPIC=AI news" in content
    assert get_settings().pexels_api_key == "px-123", "applies live, not just on restart"


async def test_unknown_and_invalid_fields_are_rejected(client, auth, env_file):
    unknown = await client.put("/v1/settings", json={"jwt_secret": "evil"}, headers=auth)
    assert unknown.status_code == 400, "only allowlisted fields are reachable"

    bad_hour = await client.put("/v1/settings", json={"youtube_daily_hour": 99}, headers=auth)
    assert bad_hour.status_code == 400

    injection = await client.put(
        "/v1/settings", json={"groq_api_key": "a b\nJARVIS_ENV=evil"}, headers=auth
    )
    assert injection.status_code == 400, "whitespace in a secret would corrupt .env"
    assert not env_file.exists()


async def test_settings_require_auth(client, env_file):
    assert (await client.get("/v1/settings")).status_code == 401


async def test_every_env_var_is_listed_with_its_current_value(client, auth, env_file):
    body = (await client.get("/v1/settings", headers=auth)).json()
    fields = {f["name"]: f for f in body["fields"]}

    # Every Settings field except the locked ones, grouped, typed, prefilled.
    from jarvis.core.config import Settings

    expected = set(Settings.model_fields) - set(body["locked"])
    assert set(fields) == expected
    assert fields["tts_voice"]["value"] == get_settings().tts_voice
    assert fields["tts_voice"]["section"] == "Voice"
    assert fields["enable_paid_llm"]["kind"] == "bool"
    assert fields["heartbeat_minutes"]["kind"] == "float"
    assert fields["env"] if "env" in fields else True  # locked: never listed
    assert fields["slack_signing_secret"]["secret"] is True
    assert fields["slack_bot_token"]["env"] == "JARVIS_SLACK_BOT_TOKEN"
    assert "jwt_secret" not in fields and "database_url" not in fields


async def test_any_listed_field_can_be_set_with_its_type_enforced(client, auth, env_file):
    s = get_settings()
    before = (s.enable_paid_llm, s.heartbeat_minutes, s.tts_voice, s.slack_signing_secret)
    try:
        updated = (
            await client.put(
                "/v1/settings",
                headers=auth,
                json={
                    "enable_paid_llm": "true",
                    "heartbeat_minutes": "15",
                    "tts_voice": "en-US-AndrewMultilingualNeural",
                    "slack_signing_secret": "sig-abc",
                },
            )
        ).json()
        by_name = {f["name"]: f["value"] for f in updated["fields"]}
        assert by_name["enable_paid_llm"] is True
        assert by_name["heartbeat_minutes"] == 15.0
        assert by_name["tts_voice"] == "en-US-AndrewMultilingualNeural"
        assert s.slack_signing_secret == "sig-abc", "applies live"
        content = env_file.read_text()
        assert "JARVIS_ENABLE_PAID_LLM=true" in content
        assert "JARVIS_HEARTBEAT_MINUTES=15.0" in content
        assert "JARVIS_SLACK_SIGNING_SECRET=sig-abc" in content

        wrong_type = await client.put(
            "/v1/settings", headers=auth, json={"heartbeat_minutes": "soon"}
        )
        assert wrong_type.status_code == 400
        locked = await client.put(
            "/v1/settings", headers=auth, json={"database_url": "postgresql://evil"}
        )
        assert locked.status_code == 400
    finally:
        s.enable_paid_llm, s.heartbeat_minutes, s.tts_voice, s.slack_signing_secret = before


async def test_a_saved_setting_is_shared_by_every_process(client, auth, env_file, session):
    """The worker is another container: it must read what the app saved, not the API's
    memory."""
    from jarvis.core.overrides import apply_overrides
    from jarvis.db.models.ops import SettingOverride
    from sqlalchemy import select

    s = get_settings()
    before = s.tts_voice
    try:
        await client.put("/v1/settings", headers=auth, json={"tts_voice": "en-GB-SoniaNeural"})
        row = await session.scalar(
            select(SettingOverride).where(SettingOverride.name == "tts_voice")
        )
        assert row is not None and row.value == "en-GB-SoniaNeural"

        # Another process: its settings still say the old thing until it lays the rows over.
        s.tts_voice = before
        assert await apply_overrides(session) >= 1
        assert s.tts_voice == "en-GB-SoniaNeural"
    finally:
        s.tts_voice = before
        row = await session.scalar(
            select(SettingOverride).where(SettingOverride.name == "tts_voice")
        )
        if row is not None:
            await session.delete(row)
            await session.commit()
