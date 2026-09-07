"""Avatar asset uploads: the seams are validation and replacement, not storage."""

from __future__ import annotations

import pytest
from jarvis.core.config import get_settings

PASSWORD = "correct-horse-battery-staple"  # noqa: S105


@pytest.fixture
async def auth(client):
    await client.post(
        "/v1/auth/register", json={"email": "video@example.com", "password": PASSWORD}
    )
    tokens = (
        await client.post(
            "/v1/auth/login", json={"email": "video@example.com", "password": PASSWORD}
        )
    ).json()
    return {"Authorization": f"Bearer {tokens['access_token']}"}


@pytest.fixture
def workdir(tmp_path):
    settings = get_settings()
    original = settings.youtube_workdir
    settings.youtube_workdir = str(tmp_path)
    yield tmp_path
    settings.youtube_workdir = original


async def test_upload_list_and_replace(client, auth, workdir):
    upload = await client.post(
        "/v1/youtube/assets",
        data={"kind": "voice"},
        files={"file": ("me.wav", b"RIFF....WAVE", "audio/wav")},
        headers=auth,
    )
    assert upload.status_code == 201
    assert upload.json() == {"kind": "voice", "filename": "voice.wav", "bytes": 12}

    listed = (await client.get("/v1/youtube/assets", headers=auth)).json()
    assert listed["assets"]["voice"]["filename"] == "voice.wav"
    assert listed["assets"]["avatar"] is None

    # A re-upload with a different container replaces, never accumulates.
    again = await client.post(
        "/v1/youtube/assets",
        data={"kind": "voice"},
        files={"file": ("me.mp3", b"ID3....", "audio/mpeg")},
        headers=auth,
    )
    assert again.status_code == 201
    files = list(workdir.glob("assets/*/voice.*"))
    assert [f.name for f in files] == ["voice.mp3"]


async def test_upload_rejects_bad_kind_and_type(client, auth, workdir):
    bad_kind = await client.post(
        "/v1/youtube/assets",
        data={"kind": "thumbnail"},
        files={"file": ("x.png", b"x", "image/png")},
        headers=auth,
    )
    assert bad_kind.status_code == 400

    bad_type = await client.post(
        "/v1/youtube/assets",
        data={"kind": "avatar"},
        files={"file": ("x.exe", b"MZ", "application/octet-stream")},
        headers=auth,
    )
    assert bad_type.status_code == 400
    assert not list(workdir.glob("assets/*/*"))


async def test_assets_require_auth(client, workdir):
    response = await client.get("/v1/youtube/assets")
    assert response.status_code == 401
