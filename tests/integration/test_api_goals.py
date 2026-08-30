"""The goal engine over HTTP, end to end."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

PASSWORD = "correct-horse-battery-staple"  # noqa: S105


@pytest.fixture
async def auth(client):
    await client.post("/v1/auth/register", json={"email": "api@example.com", "password": PASSWORD})
    tokens = (
        await client.post("/v1/auth/login", json={"email": "api@example.com", "password": PASSWORD})
    ).json()
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def test_goal_to_prediction_over_http(client, auth):
    """The demo path: create a goal, decompose it, ask whether it will land."""
    deadline = (datetime.now(UTC) + timedelta(hours=3, minutes=20)).isoformat()
    goal = (
        await client.post(
            "/v1/goals",
            json={
                "title": "Hackathon submission",
                "deadline": deadline,
                "timezone": "Asia/Kolkata",
            },
            headers=auth,
        )
    ).json()

    for title, minutes, optional in [
        ("Finish submission write-up", 150, False),
        ("Record demo video", 80, False),
        ("Alexa animation", 60, True),
        ("Knowledge-graph visualization", 70, True),
    ]:
        response = await client.post(
            "/v1/tasks",
            json={
                "title": title, "goal_id": goal["id"],
                "estimate_minutes": minutes, "is_optional": optional,
            },
            headers=auth,
        )
        assert response.status_code == 201

    prediction = (
        await client.get(f"/v1/goals/{goal['id']}/prediction", headers=auth)
    ).json()

    assert prediction["severity"] == "critical"
    assert prediction["probability"] < 0.2
    assert "usable minutes" in prediction["explanation"]
    assert "raises the predicted completion probability" in prediction["explanation"]

    keys = {o["key"] for o in prediction["options"]}
    assert "reduce_scope" in keys
    drop = next(o for o in prediction["options"] if o["key"] == "reduce_scope")
    assert set(drop["tasks_affected"]) == {"Alexa animation", "Knowledge-graph visualization"}
    assert drop["probability_after"] > prediction["probability"]


async def test_if_match_enforces_the_task_version(client, auth):
    task = (
        await client.post(
            "/v1/tasks", json={"title": "T", "estimate_minutes": 30}, headers=auth
        )
    ).json()

    ok = await client.patch(
        f"/v1/tasks/{task['id']}", json={"title": "Renamed"},
        headers=auth | {"If-Match": str(task["version"])},
    )
    assert ok.status_code == 200
    assert ok.json()["version"] == task["version"] + 1

    stale = await client.patch(
        f"/v1/tasks/{task['id']}", json={"title": "Clobbered"},
        headers=auth | {"If-Match": str(task["version"])},
    )
    assert stale.status_code == 409


async def test_acknowledge_cancels_later_alerts_over_http(client, auth):
    due = (datetime.now(UTC) + timedelta(days=2)).isoformat()
    task = (
        await client.post("/v1/tasks", json={"title": "Submit", "due_at": due}, headers=auth)
    ).json()

    result = await client.post(f"/v1/tasks/{task['id']}/acknowledge", headers=auth)
    assert result.json()["cancelled_alerts"] == 4


async def test_simulation_previews_without_acting(client, auth):
    preview = (
        await client.post(
            "/v1/actions/simulate",
            json={
                "tool": "message.send",
                "args": {"channel": "telegram", "to": "@team", "body": "Running late."},
            },
            headers=auth,
        )
    ).json()

    assert preview["risk"] == "R2"
    assert preview["policy"]["decision"] == "require_approval"
    assert preview["expected_evidence"] == ["provider_object_id"]
    assert "@team" in preview["recipients"]
    assert preview["payload_hash"].startswith("sha256:")


async def test_simulation_shows_the_exact_command_for_a_template(client, auth):
    preview = (
        await client.post(
            "/v1/actions/simulate",
            json={
                "tool": "mac.run_template",
                "args": {"template": "git.pull", "params": {"path": "/Users/p/my project"}},
            },
            headers=auth,
        )
    ).json()
    assert preview["command_preview"] == "git -C '/Users/p/my project' pull --ff-only"


async def test_kill_switch_preserves_evidence(client, auth):
    result = (await client.post("/v1/agent/pause?reason=demo", headers=auth)).json()
    assert result["paused"] is True
    assert result["evidence_preserved"] is True

    # Restore, so the flag does not leak into the next test.
    from jarvis.core.config import get_settings

    get_settings().global_pause = False


async def test_goals_are_scoped_to_their_owner(client, auth):
    goal = (await client.post("/v1/goals", json={"title": "Private"}, headers=auth)).json()

    await client.post(
        "/v1/auth/register", json={"email": "other@example.com", "password": PASSWORD}
    )
    other = (
        await client.post(
            "/v1/auth/login", json={"email": "other@example.com", "password": PASSWORD}
        )
    ).json()

    response = await client.get(
        f"/v1/goals/{goal['id']}", headers={"Authorization": f"Bearer {other['access_token']}"}
    )
    assert response.status_code == 404
