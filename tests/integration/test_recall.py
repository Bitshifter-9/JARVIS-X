"""Instant contextual recall: ask your life, get a sourced answer (#23)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from jarvis.db.models.source import SourceObject
from jarvis.services.identity import IdentityService
from jarvis.services.recall import ask_life

PASSWORD = "correct-horse-battery-staple"  # noqa: S105


class _FakeResp:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeRouter:
    """A stand-in LLM: echoes what it was asked so the test can see the grounding reached it."""

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.saw: str | None = None

    async def chat(self, messages, *, user_id=None, **kwargs):  # noqa: ANN001
        if self.fail:
            raise RuntimeError("no model")
        self.saw = messages[-1].content
        return _FakeResp("It uses cursor pagination [1].")


@pytest.fixture
async def user(session):
    u = await IdentityService(session).register("recall@example.com", PASSWORD)
    await session.commit()
    return u


async def _mail(session, uid, oid, subject, body):
    session.add(SourceObject(user_id=uid, provider="gmail", object_id=oid, kind="email",
                             title=subject, author="lead@work.com", excerpt=body,
                             occurred_at=datetime.now(UTC)))
    await session.flush()


async def test_answers_from_sources_with_citation(session, user):
    await _mail(session, user.id, "m1", "API design decision",
                "We agreed the API uses cursor pagination, not offsets.")
    await session.flush()

    router = _FakeRouter()
    out = await ask_life(session, user.id, "What did we decide about API pagination?",
                         router=router)
    assert out["grounded"] is True
    assert out["answer"] == "It uses cursor pagination [1]."
    assert out["sources"] and out["sources"][0]["n"] == 1
    # The source content actually reached the model.
    assert "cursor pagination" in (router.saw or "")


async def test_degrades_to_sources_when_no_model(session, user):
    await _mail(session, user.id, "m1", "API design decision", "cursor pagination it is")
    await session.flush()

    out = await ask_life(session, user.id, "pagination decision", router=_FakeRouter(fail=True))
    assert out["grounded"] is True
    assert out["answer"] is None  # model failed
    assert out["sources"]  # but the sources are still the answer


async def test_no_hits_is_not_grounded(session, user):
    out = await ask_life(session, user.id, "something never captured", router=_FakeRouter())
    assert out == {"answer": None, "sources": [], "grounded": False}
