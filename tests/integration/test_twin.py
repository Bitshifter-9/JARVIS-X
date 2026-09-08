"""Digital twin: answer as you (#19)."""

from __future__ import annotations

import pytest
from jarvis.services.identity import IdentityService
from jarvis.services.profile import get_profile

PASSWORD = "correct-horse-battery-staple"  # noqa: S105


class _Resp:
    def __init__(self, text): self.text = text  # noqa: E704


class _Router:
    def __init__(self): self.saw = None  # noqa: E704

    async def chat(self, messages, *, user_id=None, **kwargs):  # noqa: ANN001
        self.saw = messages[-1].content
        return _Resp("Honestly, I'd just ship it.")


@pytest.fixture
async def user(session):
    u = await IdentityService(session).register("twin@example.com", PASSWORD)
    await session.commit()
    return u


async def test_twin_needs_a_profile(session, user):
    from jarvis.services.twin import answer_as_you

    out = await answer_as_you(session, user.id, "What should I do?", router=_Router())
    assert out["grounded"] is False  # nothing learned yet


async def test_twin_answers_in_your_voice(session, user):
    from jarvis.services.twin import answer_as_you

    profile = await get_profile(session, user.id)
    profile.learned_style = "Casual and brief; opens with 'hey', signs off 'thanks'."
    profile.about = "A founder who ships fast."
    await session.flush()

    router = _Router()
    out = await answer_as_you(session, user.id, "Should we launch Friday?", router=router)
    assert out["grounded"] is True
    assert out["answer"] == "Honestly, I'd just ship it."
    # The learned style actually reached the model.
    assert "ship fast" in router.saw or "brief" in router.saw
