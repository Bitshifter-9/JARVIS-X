"""The Alexa skill endpoint (PLAN.md phase 4).

**Substitution, noted:** the blueprint puts the skill behind an ASK SDK Lambda. We serve
it from the same FastAPI process over HTTPS instead — Alexa supports either, and this
removes a second language, a second deployment and an AWS account from the critical path.
What Lambda would have given us for free (request authenticity) is implemented in
``connectors/alexa/verify.py`` and tested there.

Three gates, in order, before a single word is interpreted:

1. **Signature** — the request really came from Amazon.
2. **Skill id** — it was addressed to *our* skill, not another developer's.
3. **Account link** — the speaker maps to a JARVIS account, via an access token our own
   OAuth2 server issued.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

import httpx
import jwt
from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse

from jarvis.api.deps import SessionDep
from jarvis.connectors.alexa.skill import handle_request, speak
from jarvis.connectors.alexa.verify import AlexaSignatureError, verify_request, verify_timestamp
from jarvis.core.config import get_settings
from jarvis.core.logging import get_logger
from jarvis.core.security import decode_access_token
from jarvis.db.models.identity import User

log = get_logger(__name__)
router = APIRouter(prefix="/alexa", tags=["alexa"])

# Amazon rotates these rarely and signs every request with one. Caching by URL keeps a
# skill invocation to one round trip instead of two. Bounded, because the URL is
# attacker-influenced input even after the host check.
_CERT_CACHE: dict[str, bytes] = {}
_CERT_CACHE_MAX = 8


async def _certificate_chain(url: str) -> bytes:
    if cached := _CERT_CACHE.get(url):
        return cached
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(url)
    response.raise_for_status()
    if len(_CERT_CACHE) >= _CERT_CACHE_MAX:
        _CERT_CACHE.clear()
    _CERT_CACHE[url] = response.content
    return response.content


async def _resolve_user(session, body: dict[str, Any]) -> uuid.UUID | None:  # noqa: ANN001
    """The linked account, or ``None``.

    The access token is one our own authorization server issued during account linking,
    so this is the same check every other authenticated route performs.
    """
    token = (((body.get("session") or {}).get("user")) or {}).get("accessToken")
    if not token:
        return None
    try:
        claims = decode_access_token(token)
        user_id = uuid.UUID(claims["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        log.warning("alexa_bad_access_token")
        return None

    user = await session.get(User, user_id)
    if user is None or not user.is_active:
        return None
    return user_id


@router.post("")
async def alexa_endpoint(
    request: Request,
    session: SessionDep,
    signature: Annotated[str | None, Header(alias="Signature-256")] = None,
    certificate_url: Annotated[str | None, Header(alias="SignatureCertChainUrl")] = None,
) -> JSONResponse:
    settings = get_settings()
    raw = await request.body()

    try:
        body: dict[str, Any] = await request.json()
    except (ValueError, UnicodeDecodeError):
        return JSONResponse(speak("I could not understand that request."), status_code=400)

    verifying = settings.alexa_verify_signature or settings.env not in ("local", "test")
    if verifying:
        try:
            verify_timestamp((body.get("request") or {}).get("timestamp"))
            if not certificate_url:
                raise AlexaSignatureError("request carries no certificate chain URL")
            await _verify(raw, signature, certificate_url)
        except AlexaSignatureError as exc:
            # 403 with no detail: an attacker learns nothing about which gate closed.
            log.warning("alexa_request_rejected", reason=str(exc))
            return JSONResponse({"error": "forbidden"}, status_code=403)

    # A request naming another skill is not ours to answer, however well signed.
    application_id = (
        ((body.get("session") or {}).get("application") or {}).get("applicationId")
        or ((body.get("context") or {}).get("System", {}).get("application") or {}).get(
            "applicationId"
        )
    )
    if settings.alexa_skill_id and application_id != settings.alexa_skill_id:
        log.warning("alexa_wrong_skill_id")
        return JSONResponse({"error": "forbidden"}, status_code=403)

    user_id = await _resolve_user(session, body)
    response = await handle_request(session, body, user_id=user_id)
    return JSONResponse(response)


async def _verify(raw: bytes, signature: str | None, certificate_url: str) -> None:
    from jarvis.connectors.alexa.verify import verify_certificate_url

    # Shape first: the URL is checked *before* it is fetched, so a hostile URL is never
    # dialled at all.
    verify_certificate_url(certificate_url)
    try:
        chain = await _certificate_chain(certificate_url)
    except httpx.HTTPError as exc:
        raise AlexaSignatureError("certificate chain could not be fetched") from exc
    verify_request(
        body=raw,
        signature=signature,
        certificate_chain_pem=chain,
        certificate_url=certificate_url,
    )
