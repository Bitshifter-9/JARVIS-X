"""Phase 2.9: contract fuzzing and hostile-input handling.

Two layers, because one of them has a real limitation worth naming.

**Schemathesis** checks the OpenAPI document itself and generates requests from it. It
does not *execute* them here: it drives an ASGI app synchronously, which runs our
lifespan and disposes the async engine on a foreign event loop. That produces failures
that look like server crashes but are not, which is worse than no coverage. So it is used
for what it is uniquely good at in this setup — proving the spec our Dart, Python and TS
clients are generated from is complete and that valid requests can be constructed from it.

**Hand-written async cases** carry the runtime coverage of the unauthenticated surface.
Less imaginative than a generator, but they exercise the real path.
"""

from __future__ import annotations

import pytest
import schemathesis
from hypothesis import HealthCheck, given, settings
from jarvis.main import create_app

app = create_app()
schema = schemathesis.openapi.from_asgi("/openapi.json", app)


def test_every_operation_is_documented_well_enough_to_generate_a_client():
    """A missing response schema silently produces an untyped client method."""
    operations = list(schema.get_all_operations())
    assert len(operations) >= 25

    undocumented = []
    for result in operations:
        operation = result.ok()
        if not operation.definition.raw.get("responses"):
            undocumented.append(f"{operation.method.upper()} {operation.path}")
    assert not undocumented, f"operations without documented responses: {undocumented}"


@settings(max_examples=8, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(case=schema["/v1/auth/login"]["POST"].as_strategy())
def test_valid_requests_can_be_generated_from_the_spec(case):
    """If a request cannot be built from the spec, no generated client can call it."""
    assert case.path == "/v1/auth/login"
    assert case.method.upper() == "POST"


# ── the DB-backed public surface, by hand ──────────────────────────────
HOSTILE_LOGIN = [
    pytest.param({"email": "a" * 5000 + "@x.test", "password": "x" * 5000}, id="oversized"),
    pytest.param({"email": None, "password": None}, id="nulls"),
    pytest.param({"email": {"nested": "object"}, "password": ["list"]}, id="wrong-types"),
    pytest.param({}, id="empty"),
    pytest.param({"email": "a@b.test", "password": "\U0001f600" * 500}, id="emoji"),
    pytest.param({"email": "a@b.test", "password": "p\u202en"}, id="bidi-override"),
    pytest.param({"email": "' OR 1=1 --@x.test", "password": "x" * 12}, id="sql-shaped"),
    pytest.param({"email": "<script>alert(1)</script>@x.test", "password": "x" * 12}, id="html"),
]


@pytest.mark.parametrize("payload", HOSTILE_LOGIN)
async def test_hostile_login_payloads_produce_problem_documents(client, payload):
    """Malformed input is a client error with a readable shape, never a stack trace."""
    response = await client.post("/v1/auth/login", json=payload)
    assert 400 <= response.status_code < 500
    assert response.headers["content-type"].startswith("application/problem+json")
    assert "correlation_id" in response.json()


@pytest.mark.parametrize("payload", HOSTILE_LOGIN)
async def test_hostile_register_payloads_produce_problem_documents(client, payload):
    response = await client.post("/v1/auth/register", json=payload)
    assert 400 <= response.status_code < 500


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"client_id": "x"},
        {"client_id": "x", "redirect_uri": "not-a-url"},
        {"client_id": "\U0001f600", "redirect_uri": "https://x.test", "response_type": "token"},
        {"client_id": "x" * 5000, "redirect_uri": "https://x.test"},
    ],
)
async def test_malformed_authorize_requests_never_crash(client, params):
    response = await client.get("/oauth/authorize", params=params)
    assert response.status_code < 500


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"grant_type": "authorization_code"},
        {"grant_type": "\U0001f600"},
        {"grant_type": "refresh_token", "refresh_token": "x" * 10_000},
    ],
)
async def test_malformed_token_requests_never_crash(client, payload):
    response = await client.post("/oauth/token", data=payload)
    assert response.status_code < 500


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"callback_query": {}},
        {"callback_query": {"id": "1", "data": "approve:not-a-uuid", "message": {}}},
        {"callback_query": {"id": "1", "data": "x" * 10_000, "message": {"chat": {"id": "9"}}}},
        {"message": {"chat": {"id": "9"}, "text": "delete everything"}},
    ],
)
async def test_malformed_telegram_updates_never_crash(client, payload):
    """A non-200 makes Telegram retry, so even hostile input must be answered calmly."""
    response = await client.post("/webhooks/telegram", json=payload)
    assert response.status_code == 200


async def test_unknown_routes_are_problem_documents_too(client):
    response = await client.get("/v1/does-not-exist")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")


@pytest.mark.parametrize(
    "header", ["Bearer", "Bearer ", "Bearer ...", "Basic abc", "Bearer a.b.c", ""]
)
async def test_a_malformed_token_never_crashes_the_auth_dependency(client, header):
    response = await client.get("/v1/auth/me", headers={"Authorization": header})
    assert response.status_code in (401, 403)
    assert response.headers["content-type"].startswith("application/problem+json")
