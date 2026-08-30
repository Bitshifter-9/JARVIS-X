"""Provider webhook ingress.

A webhook acknowledges fast, stores minimal metadata and enqueues (blueprint §3). It
never calls the LLM synchronously — a provider that times out waiting for inference
retries, and the retry storm is worse than the latency.

These routes authenticate by **provider signature, not by user session**, so they must
verify that signature themselves before doing anything.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, Request, Response, status

from jarvis.connectors.telegram.client import TelegramClient
from jarvis.connectors.telegram.service import TelegramService
from jarvis.core.config import get_settings
from jarvis.core.logging import get_logger
from jarvis.core.security import tokens_equal

log = get_logger(__name__)
router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/telegram")
async def telegram_webhook(
    request: Request,
    secret_token: str | None = Header(default=None, alias="X-Telegram-Bot-Api-Secret-Token"),
) -> Response:
    """Telegram update ingress.

    Telegram echoes a secret we set on ``setWebhook``; comparing it is what distinguishes
    a real update from anyone who found the URL. Always 200: a non-200 makes Telegram
    retry, and retrying a hostile request helps nobody.
    """
    settings = get_settings()
    expected = settings.telegram_webhook_secret
    if expected and not (secret_token and tokens_equal(secret_token, expected)):
        log.warning("telegram_webhook_bad_secret")
        return Response(status_code=status.HTTP_200_OK)

    update: dict[str, Any] = await request.json()

    from jarvis.db.session import session_scope

    async with session_scope() as session:
        service = TelegramService(session, TelegramClient(settings.telegram_bot_token))
        outcome = await service.handle_update(update)

    log.info("telegram_update", handled=outcome.handled)
    return Response(status_code=status.HTTP_200_OK)
