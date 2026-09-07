"""The Mac (and phone) as a hand: typed verbs, policy-gated, verified, delivered.

"Open Chrome, send a WhatsApp, take a screenshot, send me that file" — each is a typed
action with a risk, an allowlist check at the helper, and an observation the verifier
judges. What comes back as pixels or bytes is stored server-side as an artifact and
pushed to the owner's Telegram; the app's Timeline shows all of it.
"""

from __future__ import annotations

import io
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from jarvis.connectors.telegram.client import RecordingTransport
from jarvis.connectors.telegram.service import TelegramService
from jarvis.db.models.agent import Action, ActionStatus, Approval
from jarvis.db.models.job import Job
from jarvis.db.models.ops import Artifact
from jarvis.services.device import DeviceService, JobEnvelope, generate_keypair, sign
from jarvis.services.evidence.verifier import EvidenceRequirement, check_requirement
from jarvis.services.identity import IdentityService
from jarvis.services.policy import Decision, PolicyService, ProposalContext
from macnode.adapters import CaptureResult, FakeMacAdapter, applescript_quote
from macnode.executor import WHATSAPP, Executor
from macnode.guard import JobGuard, LocalPolicy
from macnode.voice import STOP_PHRASES, VoiceLoop
from sqlalchemy import select

PASSWORD = "correct-horse-battery-staple"  # noqa: S105
CHROME = "com.google.Chrome"
CHAT_ID = "31337"


@pytest.fixture
def server_keys():
    return generate_keypair()


@pytest.fixture
def device_keys():
    return generate_keypair()


@pytest.fixture
async def user(session):
    u = await IdentityService(session).register("hand@example.com", PASSWORD)
    await session.commit()
    return u


@pytest.fixture
async def auth(client, user):
    r = await client.post(
        "/v1/auth/login", json={"email": "hand@example.com", "password": PASSWORD}
    )
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture
async def mac(session, user, device_keys):
    private_pem, public_pem = device_keys
    devices = DeviceService(session)
    challenge = await devices.begin_pairing(
        user.id,
        name="MacBook",
        public_key_pem=public_pem,
        allowed_bundle_ids=[CHROME, WHATSAPP],
        capabilities=["mac.open_app"],
    )
    await session.commit()
    device = await devices.complete_pairing(
        user.id,
        challenge=challenge.challenge,
        signature=sign(private_pem, challenge.challenge.encode()),
    )
    await session.commit()
    return device


def _envelope(server_private, action: str, args: dict, **overrides) -> JobEnvelope:
    base = {
        "job_id": f"job_{uuid.uuid4().hex[:6]}",
        "action": action,
        "args": args,
        "risk": "R1",
        "nonce": uuid.uuid4().hex,
        "issued_at": datetime.now(UTC).isoformat(),
        "expires_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
        "policy_version": 1,
        "device_id": "device-1",
    }
    base.update(overrides)
    envelope = JobEnvelope(**base)
    return JobEnvelope(**{**base, "signature": sign(server_private, envelope.signing_payload())})


def _executor(server_public, device_private, adapter, *, uploader=None, **policy):
    return Executor(
        adapter=adapter,
        guard=JobGuard(
            server_public_pem=server_public,
            policy=LocalPolicy(allowed_bundle_ids={CHROME, WHATSAPP}, **policy),
        ),
        device_private_pem=device_private,
        frontmost_timeout=0.2,
        uploader=uploader,
    )


# ── policy: what the server lets through ───────────────────────────────
@pytest.mark.parametrize(
    ("tool", "args", "expected"),
    [
        ("mac.open_url", {"url": "https://example.test"}, Decision.ALLOW),
        ("mac.open_url", {"url": "whatsapp://send?phone=1&text=hi"}, Decision.ALLOW),
        ("mac.open_url", {"url": "file:///etc/passwd"}, Decision.DENY),
        ("mac.open_url", {"url": "javascript:alert(1)"}, Decision.DENY),
        ("mac.notify", {"title": "t", "body": "b"}, Decision.ALLOW),
        ("mac.lock_screen", {}, Decision.ALLOW),
        ("mac.type_text", {"bundle_id": CHROME, "text": "hi"}, Decision.REQUIRE_APPROVAL),
        ("mac.type_text", {"bundle_id": "com.apple.Terminal", "text": "rm"}, Decision.DENY),
        ("mac.capture_screen", {}, Decision.REQUIRE_APPROVAL),
        (
            "mac.send_file",
            {"path": "/Users/p/Docs/a.pdf", "scope_bookmark": "/Users/p/Docs"},
            Decision.REQUIRE_APPROVAL,
        ),
        ("mac.whatsapp_send", {"phone": "+91 99999", "text": "late"}, Decision.REQUIRE_APPROVAL),
        ("phone.open_url", {"url": "https://example.test"}, Decision.ALLOW),
        ("phone.whatsapp_draft", {"phone": "1", "text": "hi"}, Decision.ALLOW),
    ],
)
async def test_the_hand_is_typed_and_ranked(session, user, mac, tool, args, expected):
    result = await PolicyService(session).evaluate(
        await PolicyService(session).load_context(user.id, tool, args, device_id=mac.id)
    )
    assert result.decision is expected, result.reason


async def test_a_hand_action_needs_a_paired_owner_device(session, user):
    result = await PolicyService(session).evaluate(
        ProposalContext(user_id=user.id, tool="mac.notify", args={"title": "t", "body": "b"})
    )
    assert result.decision is Decision.DENY


# ── verifier: the new evidence kinds ───────────────────────────────────
def test_url_opened_needs_the_os_to_have_taken_it():
    req = EvidenceRequirement(kind="url_opened", value="https://example.test/a")
    assert check_requirement(req, {}).verdict.value == "inconclusive"
    assert check_requirement(req, {"opened": False}).verdict.value == "failed"
    assert (
        check_requirement(
            req, {"opened": True, "opened_url": "https://example.test/a"}
        ).verdict.value
        == "verified"
    )
    assert (
        check_requirement(req, {"opened": True, "opened_url": "https://evil.test/"}).verdict.value
        == "failed"
    )


def test_an_artifact_must_actually_have_reached_the_server():
    req = EvidenceRequirement(kind="artifact_uploaded", value=None)
    assert check_requirement(req, {"artifact_id": None}).verdict.value == "failed"
    assert check_requirement(req, {"artifact_id": "abc"}).verdict.value == "verified"


# ── the helper: what the Mac actually does ─────────────────────────────
def test_applescript_quoting_keeps_a_quote_from_ending_the_string():
    assert applescript_quote('say "hi" \\ bye') == 'say \\"hi\\" \\\\ bye'


def test_opening_a_url_reports_what_came_forward(server_keys, device_keys):
    sp, spub = server_keys
    dp, _ = device_keys
    adapter = FakeMacAdapter(installed={CHROME}, url_handlers={"https": CHROME})
    result = _executor(spub, dp, adapter).handle(
        _envelope(sp, "mac.open_url", {"url": "https://example.test"})
    )
    assert result.status == "completed"
    assert result.observed == {
        "opened": True,
        "opened_url": "https://example.test",
        "frontmost_bundle_id": CHROME,
    }


def test_a_scheme_the_mac_did_not_enable_is_refused_at_the_helper(server_keys, device_keys):
    sp, spub = server_keys
    dp, _ = device_keys
    adapter = FakeMacAdapter()
    result = _executor(spub, dp, adapter).handle(
        _envelope(sp, "mac.open_url", {"url": "file:///etc/passwd"})
    )
    assert result.status == "rejected" and adapter.opened_urls == []


def test_typing_goes_only_into_the_named_app(server_keys, device_keys):
    """If the app will not come forward, nothing is typed into whatever is there instead."""
    sp, spub = server_keys
    dp, _ = device_keys
    adapter = FakeMacAdapter(installed={CHROME}, refuse_frontmost={CHROME})
    adapter.launch(CHROME)
    adapter.frontmost = "com.apple.Terminal"
    adapter.refuse_frontmost.add(CHROME)

    class Stubborn(FakeMacAdapter):
        def activate(self, bundle_id):  # noqa: ANN001, ANN202
            return self.running(bundle_id)

    stubborn = Stubborn(installed={CHROME}, frontmost="com.apple.Terminal")
    stubborn.running_apps[CHROME] = adapter.running_apps[CHROME]
    result = _executor(spub, dp, stubborn).handle(
        _envelope(sp, "mac.type_text", {"bundle_id": CHROME, "text": "hello"}, risk="R2")
    )
    assert result.observed["typed_chars"] == 0
    assert stubborn.typed == []


def test_typing_into_the_front_app_reports_the_count(server_keys, device_keys):
    sp, spub = server_keys
    dp, _ = device_keys
    adapter = FakeMacAdapter(installed={CHROME})
    adapter.launch(CHROME)
    result = _executor(spub, dp, adapter).handle(
        _envelope(sp, "mac.type_text", {"bundle_id": CHROME, "text": "hello"}, risk="R2")
    )
    assert result.observed["typed_chars"] == 5
    assert result.observed["frontmost_bundle_id"] == CHROME
    assert adapter.typed == ["hello"]


def test_a_revoked_accessibility_permission_types_nothing(server_keys, device_keys):
    sp, spub = server_keys
    dp, _ = device_keys
    adapter = FakeMacAdapter(installed={CHROME}, accessibility=False)
    adapter.launch(CHROME)
    result = _executor(spub, dp, adapter).handle(
        _envelope(sp, "mac.press_key", {"bundle_id": CHROME, "key": "return"}, risk="R2")
    )
    assert result.observed == {"permission": "accessibility_denied", "pressed_key": None}


def test_a_screenshot_is_uploaded_and_only_its_id_travels(server_keys, device_keys, tmp_path):
    sp, spub = server_keys
    dp, _ = device_keys
    shot = tmp_path / "shot.png"
    shot.write_bytes(b"\\x89PNG fake")
    adapter = FakeMacAdapter(
        capture=CaptureResult(digest="sha256:abc", width=1440, height=900, path=str(shot))
    )
    uploads: list[tuple[str, str]] = []

    def uploader(path, *, kind, job_id):  # noqa: ANN001, ANN202
        uploads.append((path, kind))
        return "art_123"

    result = _executor(spub, dp, adapter, uploader=uploader).handle(
        _envelope(sp, "mac.capture_screen", {}, risk="R2")
    )
    assert result.observed["artifact_id"] == "art_123"
    assert result.observed["digest"] == "sha256:abc"
    assert uploads == [(str(shot), "screenshot")]
    assert "pixels" not in str(result.observed)


def test_a_file_outside_the_granted_directory_is_never_sent(server_keys, device_keys, tmp_path):
    sp, spub = server_keys
    dp, _ = device_keys
    granted = tmp_path / "Docs"
    granted.mkdir()
    secret = tmp_path / "secret.key"
    secret.write_text("k")
    uploads: list[str] = []
    adapter = FakeMacAdapter(scoped_files={str(secret)})
    result = _executor(spub, dp, adapter, uploader=lambda p, **_: uploads.append(p) or "x").handle(
        _envelope(
            sp,
            "mac.send_file",
            {"path": str(granted / ".." / "secret.key"), "scope_bookmark": str(granted)},
            risk="R2",
        )
    )
    assert result.observed["status"] == 403 and uploads == []


def test_whatsapp_send_presses_return_only_once_whatsapp_is_in_front(server_keys, device_keys):
    sp, spub = server_keys
    dp, _ = device_keys
    adapter = FakeMacAdapter(installed={WHATSAPP}, url_handlers={"whatsapp": WHATSAPP})
    result = _executor(spub, dp, adapter).handle(
        _envelope(
            sp,
            "mac.whatsapp_send",
            {"phone": "+91 98765 43210", "text": "Running late!"},
            risk="R2",
        )
    )
    assert adapter.opened_urls == ["whatsapp://send?phone=919876543210&text=Running%20late%21"]
    assert result.observed["frontmost_bundle_id"] == WHATSAPP
    assert result.observed["pressed_key"] == "return"
    assert adapter.keys == [("return", [])]


def test_whatsapp_send_does_not_press_return_into_another_app(server_keys, device_keys):
    sp, spub = server_keys
    dp, _ = device_keys
    # The URL opens, but WhatsApp never comes forward — Chrome stays in front.
    adapter = FakeMacAdapter(installed={CHROME}, frontmost=CHROME)
    result = _executor(spub, dp, adapter).handle(
        _envelope(sp, "mac.whatsapp_send", {"phone": "1", "text": "x"}, risk="R2")
    )
    assert result.observed["opened"] is True
    assert result.observed["pressed_key"] is None
    assert adapter.keys == []


def test_whatsapp_send_needs_whatsapp_on_this_macs_allowlist(server_keys, device_keys):
    sp, spub = server_keys
    dp, _ = device_keys
    executor = Executor(
        adapter=FakeMacAdapter(),
        guard=JobGuard(server_public_pem=spub, policy=LocalPolicy(allowed_bundle_ids={CHROME})),
        device_private_pem=dp,
        frontmost_timeout=0.1,
    )
    result = executor.handle(
        _envelope(sp, "mac.whatsapp_send", {"phone": "1", "text": "x"}, risk="R2")
    )
    assert result.status == "rejected"


def test_media_and_volume_are_allowlisted_verbs_not_free_text(server_keys, device_keys):
    sp, spub = server_keys
    dp, _ = device_keys
    adapter = FakeMacAdapter()
    ex = _executor(spub, dp, adapter)
    ok = ex.handle(_envelope(sp, "mac.media", {"app": "Spotify", "command": "pause"}))
    bad = ex.handle(
        _envelope(sp, "mac.media", {"app": "Terminal", "command": 'do shell script "x"'})
    )
    vol = ex.handle(_envelope(sp, "mac.set_volume", {"level": 250}))
    assert ok.observed["status"] == 200 and adapter.media_calls == [("Spotify", "pause")]
    assert bad.observed["status"] == 400
    assert vol.observed["level"] == 100 and adapter.volume == 100


def test_find_files_stays_inside_the_granted_directory(server_keys, device_keys, tmp_path):
    sp, spub = server_keys
    dp, _ = device_keys
    root = str(tmp_path)
    adapter = FakeMacAdapter(file_index={root: [f"{root}/thesis.pdf", f"{root}/notes.md"]})
    result = _executor(spub, dp, adapter).handle(
        _envelope(sp, "mac.find_files", {"query": "thesis", "scope_bookmark": root})
    )
    assert result.observed["matches"] == [f"{root}/thesis.pdf"]


# ── the direct-action route, artifacts, timeline, delivery ─────────────
async def test_an_r1_mac_action_from_the_app_is_addressed_to_the_paired_mac(
    client, auth, session, mac
):
    response = await client.post(
        "/v1/actions",
        headers=auth,
        json={"tool": "mac.notify", "args": {"title": "Hi", "body": "there"}},
    )
    body = response.json()
    assert response.status_code == 202, body
    assert body["status"] == "queued"
    action = await session.get(Action, uuid.UUID(body["action_id"]))
    assert action.status == ActionStatus.DISPATCHED.value and action.device_id == mac.id


async def test_an_r2_mac_action_tapped_in_the_app_is_approved_by_the_tap_and_dispatched(
    client, auth, session, user, mac
):
    """The owner pressed the button in their own signed-in app: that is the approval for
    a device verb (PLAN.md 11.8). The decision is recorded as such, the action is
    dispatched at once, and the resume job the decision queued finds nothing left to do."""
    response = await client.post("/v1/actions", headers=auth, json={"tool": "mac.capture_screen"})
    body = response.json()
    assert body["status"] == "queued" and body["approved_by"] == "app-tap"

    action_id = uuid.UUID(body["action_id"])
    approval = await session.scalar(select(Approval).where(Approval.action_id == action_id))
    assert approval.decision == "approved" and approval.decided_by == "app-tap"
    action = await session.get(Action, uuid.UUID(body["action_id"]), populate_existing=True)
    assert action.status == ActionStatus.DISPATCHED.value

    from jarvis.workers.agent import AgentWorker

    job = await session.scalar(select(Job).where(Job.kind == "run.resume"))
    assert job is not None and job.payload["action_id"] == body["action_id"]
    outcome = await AgentWorker(checkpointer=None).handle_resume(session, job)
    assert outcome == {"skipped": "action is dispatched"}


async def test_a_denied_action_is_reported_not_swallowed(client, auth, mac):
    response = await client.post(
        "/v1/actions", headers=auth, json={"tool": "mac.open_url", "args": {"url": "file:///etc"}}
    )
    assert response.json()["status"] == "denied"


async def test_a_device_uploads_an_artifact_and_only_the_owner_can_read_it(
    client, auth, session, user, mac
):
    response = await client.post(
        f"/v1/devices/{mac.id}/artifacts",
        headers=auth,
        data={"kind": "screenshot"},
        files={"file": ("shot.png", io.BytesIO(b"\\x89PNG..."), "image/png")},
    )
    assert response.status_code == 201, response.text
    artifact = response.json()
    assert artifact["sha256"] and artifact["content_type"] == "image/png"

    mine = await client.get(artifact["url"], headers=auth)
    assert mine.status_code == 200 and mine.content == b"\\x89PNG..."

    other = await IdentityService(session).register("other@example.com", PASSWORD)
    await session.commit()
    r = await client.post(
        "/v1/auth/login", json={"email": "other@example.com", "password": PASSWORD}
    )
    theirs = await client.get(
        artifact["url"], headers={"Authorization": f"Bearer {r.json()['access_token']}"}
    )
    assert theirs.status_code == 404
    assert other.id != user.id


async def test_a_stranger_cannot_upload_as_your_mac(client, session, mac):
    other = await IdentityService(session).register("stranger@example.com", PASSWORD)
    await session.commit()
    r = await client.post(
        "/v1/auth/login", json={"email": "stranger@example.com", "password": PASSWORD}
    )
    response = await client.post(
        f"/v1/devices/{mac.id}/artifacts",
        headers={"Authorization": f"Bearer {r.json()['access_token']}"},
        data={"kind": "file"},
        files={"file": ("x.bin", io.BytesIO(b"x"), "application/octet-stream")},
    )
    assert response.status_code == 403 and other.id


async def test_a_delivered_screenshot_reaches_the_owners_telegram(client, auth, session, user, mac):
    from jarvis.api.routes.devices import deliver_artifact

    transport = RecordingTransport()
    await TelegramService(session, transport).link_chat(user.id, CHAT_ID)
    upload = await client.post(
        f"/v1/devices/{mac.id}/artifacts",
        headers=auth,
        data={"kind": "screenshot"},
        files={"file": ("shot.png", io.BytesIO(b"png"), "image/png")},
    )
    artifact_id = upload.json()["id"]
    action = Action(
        user_id=user.id,
        tool="mac.capture_screen",
        args={},
        risk="R2",
        expected=[],
        idempotency_key=f"t:{uuid.uuid4()}",
        timeout_seconds=30,
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
        status="dispatched",
        device_id=mac.id,
        policy_version=1,
    )
    session.add(action)
    await session.flush()

    assert await deliver_artifact(session, action, artifact_id, telegram=transport) is True
    method, payload = transport.calls[-1]
    assert method == "sendPhoto" and payload["chat_id"] == CHAT_ID
    stored = await session.get(Artifact, uuid.UUID(artifact_id))
    assert stored.delivered_to == {"telegram": CHAT_ID}


async def test_the_timeline_shows_actions_with_their_artifacts(client, auth, session, user, mac):
    queued = (
        await client.post(
            "/v1/actions",
            headers=auth,
            json={"tool": "mac.notify", "args": {"title": "a", "body": "b"}},
        )
    ).json()
    await client.post(
        f"/v1/devices/{mac.id}/artifacts",
        headers=auth,
        data={"kind": "screenshot", "action_id": queued["action_id"]},
        files={"file": ("s.png", io.BytesIO(b"p"), "image/png")},
    )
    rows = (await client.get("/v1/timeline", headers=auth)).json()
    action_row = next(r for r in rows if r["kind"] == "action" and r["id"] == queued["action_id"])
    assert action_row["title"] == "mac.notify" and action_row["status"] == "dispatched"
    assert action_row["artifacts"][0]["url"].startswith("/v1/artifacts/")
    assert rows == sorted(rows, key=lambda r: r["at"], reverse=True)


# ── the voice loop ─────────────────────────────────────────────────────
def test_the_voice_loop_wakes_listens_asks_and_speaks_in_that_order():
    order: list[str] = []
    loop = VoiceLoop(
        wait_for_wake=lambda: order.append("wake"),
        listen=lambda: order.append("listen") or b"audio",
        transcribe=lambda audio: order.append("transcribe") or "what's due today",
        ask=lambda text: order.append(f"ask:{text}") or "Two things are due.",
        speak=lambda reply: order.append(f"speak:{reply}"),
        indicate=lambda state: order.append(f"[{state}]"),
    )
    assert loop.run_once() == "Two things are due."
    assert order == [
        "[idle]",
        "wake",
        "[listening]",
        "listen",
        "transcribe",
        "[thinking]",
        "ask:what's due today",
        "[speaking]",
        "speak:Two things are due.",
        "[idle]",
    ]
    assert loop.transcript == [("what's due today", "Two things are due.")]


def test_silence_after_the_wake_word_asks_nothing():
    asked: list[str] = []
    loop = VoiceLoop(
        wait_for_wake=lambda: None,
        listen=lambda: b"",
        transcribe=lambda _: "  ",
        ask=lambda t: asked.append(t) or "",
        speak=lambda _: None,
    )
    assert loop.run_once() is None and asked == []


@pytest.mark.parametrize("phrase", STOP_PHRASES)
def test_a_stop_phrase_is_acknowledged_locally_and_never_sent(phrase):
    asked: list[str] = []
    spoken: list[str] = []
    loop = VoiceLoop(
        wait_for_wake=lambda: None,
        listen=lambda: b"",
        transcribe=lambda _: phrase.title() + ".",
        ask=lambda t: asked.append(t) or "",
        speak=spoken.append,
    )
    assert loop.run_once() is None
    assert asked == [] and spoken == ["Okay."]


# ── one voice everywhere ───────────────────────────────────────────────
async def test_tts_is_synthesized_once_and_cached_by_text(client, auth, monkeypatch, tmp_path):
    from jarvis.api.routes import tts as tts_routes
    from jarvis.core.config import get_settings

    monkeypatch.setattr(get_settings(), "artifact_dir", str(tmp_path / "artifacts"))
    calls: list[tuple[str, str]] = []

    async def fake_synth(text, voice, path):  # noqa: ANN001
        calls.append((text, voice))
        path.write_bytes(b"ID3fake-mp3")

    monkeypatch.setattr(tts_routes, "synthesize_to", fake_synth)

    first = await client.post("/v1/tts", headers=auth, json={"text": "Two things are due."})
    second = await client.post("/v1/tts", headers=auth, json={"text": "Two things are due."})
    assert first.status_code == 200 and first.headers["content-type"].startswith("audio/mpeg")
    assert first.content == b"ID3fake-mp3" == second.content
    assert calls == [("Two things are due.", get_settings().tts_voice)]


async def test_tts_needs_a_session(client):
    assert (await client.post("/v1/tts", json={"text": "hi"})).status_code == 401


def test_the_mac_speaker_falls_back_to_say_when_the_server_is_unreachable(monkeypatch):
    from macnode import voice as voice_module
    from macnode.voice import ServerSpeaker

    spoken: list[str] = []
    monkeypatch.setattr(voice_module, "say", spoken.append)
    played: list[bytes] = []

    def fetch_fails(_text):  # noqa: ANN001, ANN202
        raise ConnectionError("offline")

    ServerSpeaker("http://x", "t", fetch=fetch_fails, play=played.append)("Hello there")
    assert spoken == ["Hello there"] and played == []

    ServerSpeaker("http://x", "t", fetch=lambda _t: b"mp3", play=played.append)("Hello again")
    assert played == [b"mp3"] and spoken == ["Hello there"]
