"""Phase 4: Alexa request authenticity and the response envelope.

The skill is served over HTTPS, so the checks the ASK SDK would have performed inside
Lambda are ours. A skill that answers an unsigned request is a skill anyone on the
internet can drive, which is why these are unit tests with a real key pair rather than
a mocked verifier.
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import NameOID
from jarvis.connectors.alexa.skill import link_account, speak
from jarvis.connectors.alexa.verify import (
    AlexaSignatureError,
    verify_certificate_url,
    verify_request,
    verify_timestamp,
)

GOOD_URL = "https://s3.amazonaws.com/echo.api/echo-api-cert.pem"
BODY = b'{"request":{"type":"IntentRequest"}}'


def _certificate(*, san: str = "echo-api.amazon.com", expired: bool = False):
    """A throwaway leaf certificate. Real crypto, no network."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, san)])
    now = datetime.now(UTC)
    starts = now - timedelta(days=400 if expired else 1)
    ends = starts + timedelta(days=30)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(starts)
        .not_valid_after(ends)
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(san)]), critical=False)
        .sign(key, hashes.SHA256())
    )
    pem = certificate.public_bytes(serialization.Encoding.PEM)
    return key, pem


def _signature(key, body: bytes) -> str:
    return base64.b64encode(
        key.sign(body, padding.PKCS1v15(), hashes.SHA256())
    ).decode()


# ── certificate URL (the cheapest gate, so it runs first) ──────────────
@pytest.mark.parametrize(
    "url",
    [
        "http://s3.amazonaws.com/echo.api/cert.pem",
        "https://evil.example.com/echo.api/cert.pem",
        "https://s3.amazonaws.com/notecho.api/cert.pem",
        "https://s3.amazonaws.com/echo.api/../../evil/cert.pem",
        "https://s3.amazonaws.com:8443/echo.api/cert.pem",
    ],
)
def test_a_certificate_url_we_do_not_control_is_refused_before_it_is_fetched(url):
    with pytest.raises(AlexaSignatureError):
        verify_certificate_url(url)


def test_amazons_own_certificate_url_is_accepted():
    verify_certificate_url(GOOD_URL)


# ── timestamp ──────────────────────────────────────────────────────────
def test_a_fresh_timestamp_passes_and_a_captured_one_does_not():
    now = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)
    verify_timestamp("2026-09-05T12:01:00Z", now=now)
    with pytest.raises(AlexaSignatureError):
        verify_timestamp("2026-09-05T11:50:00Z", now=now)


@pytest.mark.parametrize("value", [None, "", "yesterday"])
def test_a_missing_or_unparseable_timestamp_is_a_failure(value):
    with pytest.raises(AlexaSignatureError):
        verify_timestamp(value)


# ── signature over the raw body ────────────────────────────────────────
def test_a_genuine_amazon_signature_over_the_exact_body_verifies():
    key, pem = _certificate()
    verify_request(
        body=BODY,
        signature=_signature(key, BODY),
        certificate_chain_pem=pem,
        certificate_url=GOOD_URL,
    )


def test_a_body_changed_after_signing_is_rejected():
    key, pem = _certificate()
    signature = _signature(key, BODY)
    with pytest.raises(AlexaSignatureError, match="signature does not match"):
        verify_request(
            body=BODY.replace(b"IntentRequest", b"LaunchRequest"),
            signature=signature,
            certificate_chain_pem=pem,
            certificate_url=GOOD_URL,
        )


def test_a_certificate_not_issued_for_alexa_is_rejected():
    key, pem = _certificate(san="echo-api.attacker.example")
    with pytest.raises(AlexaSignatureError, match="not issued for"):
        verify_request(
            body=BODY,
            signature=_signature(key, BODY),
            certificate_chain_pem=pem,
            certificate_url=GOOD_URL,
        )


def test_an_expired_certificate_is_rejected():
    key, pem = _certificate(expired=True)
    with pytest.raises(AlexaSignatureError, match="not currently valid"):
        verify_request(
            body=BODY,
            signature=_signature(key, BODY),
            certificate_chain_pem=pem,
            certificate_url=GOOD_URL,
        )


def test_a_missing_signature_is_rejected():
    _, pem = _certificate()
    with pytest.raises(AlexaSignatureError, match="no signature"):
        verify_request(
            body=BODY, signature=None, certificate_chain_pem=pem, certificate_url=GOOD_URL
        )


# ── response envelope ──────────────────────────────────────────────────
def test_an_unlinked_request_gets_a_link_account_card_and_nothing_else():
    response = link_account()["response"]
    assert response["card"] == {"type": "LinkAccount"}
    assert "outputSpeech" in response
    assert response["shouldEndSession"] is True


def test_a_prompt_that_expects_an_answer_keeps_the_session_open():
    response = speak("Which one?", end_session=False, reprompt="Say a task name.")["response"]
    assert response["shouldEndSession"] is False
    assert response["reprompt"]["outputSpeech"]["text"] == "Say a task name."


# ── the interaction model and the code must not drift apart ────────────
def test_every_intent_in_the_model_has_a_handler_and_the_reverse():
    """The console model and the dispatch table are two halves of one contract.

    An intent Alexa can resolve but we cannot handle is a certification failure; a
    handler no utterance reaches is dead code.
    """
    import json
    import pathlib

    from jarvis.connectors.alexa.skill import INTENTS

    model = json.loads(
        (pathlib.Path(__file__).parents[2] / "apps/alexa-skill/interaction-model.json").read_text()
    )
    declared = {
        intent["name"]
        for intent in model["interactionModel"]["languageModel"]["intents"]
        if not intent["name"].startswith("AMAZON.")
    }
    assert declared == set(INTENTS)


def test_every_custom_intent_has_sample_utterances():
    import json
    import pathlib

    model = json.loads(
        (pathlib.Path(__file__).parents[2] / "apps/alexa-skill/interaction-model.json").read_text()
    )
    for intent in model["interactionModel"]["languageModel"]["intents"]:
        if intent["name"].startswith("AMAZON."):
            continue
        assert intent["samples"], f"{intent['name']} can never be spoken"
