"""Personal phrasebook: recurring names/jargon from the user's own words (#13)."""

from __future__ import annotations

import pytest
from jarvis.db.models.chat import ChatMessage, Conversation
from jarvis.services.identity import IdentityService
from jarvis.services.phrasebook import extract_terms, phrasebook

PASSWORD = "correct-horse-battery-staple"  # noqa: S105


def test_extractor_finds_recurring_names_and_acronyms():
    terms = {t["term"]: t["count"] for t in extract_terms([
        "Email Guru Vai Sciences about the API deadline.",
        "The Guru Vai Sciences invoice is due — ping Priya about the API.",
        "Guru Vai Sciences again, and the API keys for Priya.",
        "The weather is nice and I feel great today.",
    ])}
    assert "Guru Vai Sciences" in terms
    assert "API" in terms
    assert "Priya" in terms
    # Sentence-start and common words are not vocabulary.
    assert "The" not in terms and "Email" not in terms and "Weather" not in terms
    # A one-off name doesn't make the phrasebook.
    assert all(c >= 2 for c in terms.values())


@pytest.fixture
async def user(session):
    u = await IdentityService(session).register("phrase@example.com", PASSWORD)
    await session.commit()
    return u


async def test_phrasebook_reads_the_users_own_messages(session, user):
    conv = Conversation(user_id=user.id, title="c")
    session.add(conv)
    await session.flush()
    for text in [
        "Remind me to email Guru Vai Sciences.",
        "Guru Vai Sciences needs the ISRO report.",
        "Send the ISRO report to Guru Vai Sciences today.",
    ]:
        session.add(ChatMessage(user_id=user.id, conversation_id=conv.id, role="user",
                                content=text))
    # An assistant message must NOT be mined — it's not the user's voice.
    session.add(ChatMessage(user_id=user.id, conversation_id=conv.id, role="assistant",
                            content="Contacting Blorptron Industries now."))
    await session.flush()

    terms = {t["term"] for t in await phrasebook(session, user.id)}
    assert "Guru Vai Sciences" in terms
    assert "ISRO" in terms
    assert "Blorptron Industries" not in terms  # assistant text is excluded
