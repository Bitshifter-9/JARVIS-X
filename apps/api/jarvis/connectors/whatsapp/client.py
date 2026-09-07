"""WhatsApp Cloud API — the third rung of the escalation ladder.

Outside a 24-hour customer-service window Meta accepts **only a pre-approved template**,
so this sender never composes free text. That is a constraint, but it is also the safer
shape: the message body is a fixed sentence with named parameters, which means an
untrusted deadline title cannot smuggle instructions into what the user reads.

Templates take 24-48h to approve (§13), so submit them in Phase 2 and use them here.
"""

from __future__ import annotations

from typing import Any

import httpx
from jarvis.core.logging import get_logger

log = get_logger(__name__)
API_BASE = "https://graph.facebook.com/v21.0"

# The approved utility template: "⏰ {{1}} is due {{2}}." plus a body line.
DEFAULT_TEMPLATE = "jarvis_deadline_alert"
DEFAULT_LANGUAGE = "en"

# Meta rejects newlines and tabs inside a template parameter with a 132000 error, which
# would drop the alert entirely. Collapsing is better than losing the message.
_PARAM_MAX = 900


def _clean(value: str) -> str:
    return " ".join(str(value).split())[:_PARAM_MAX]


def template_payload(
    to: str,
    *,
    title: str,
    body: str,
    template: str = DEFAULT_TEMPLATE,
    language: str = DEFAULT_LANGUAGE,
) -> dict[str, Any]:
    """The exact JSON body. Pure, so the shape is testable without a Meta account."""
    return {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "template",
        "template": {
            "name": template,
            "language": {"code": language},
            "components": [
                {
                    "type": "body",
                    "parameters": [
                        {"type": "text", "text": _clean(title)},
                        {"type": "text", "text": _clean(body)},
                    ],
                }
            ],
        },
    }


class WhatsAppSender:
    """Implements the notification sender contract: ``send(address, *, title, body)``."""

    def __init__(
        self,
        phone_number_id: str,
        access_token: str,
        *,
        template: str = DEFAULT_TEMPLATE,
        timeout: float = 15.0,
    ) -> None:
        self.phone_number_id = phone_number_id
        self.access_token = access_token
        self.template = template
        self.timeout = timeout

    async def send(  # noqa: ANN001, ARG002
        self, address: str, *, title: str, body: str, task_id=None, data=None
    ) -> dict[str, Any]:
        if not (self.phone_number_id and self.access_token):
            raise RuntimeError("WhatsApp is not configured")

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{API_BASE}/{self.phone_number_id}/messages",
                headers={"Authorization": f"Bearer {self.access_token}"},
                json=template_payload(address, title=title, body=body, template=self.template),
            )
        data = response.json()
        if response.status_code >= 400:
            # Surfaced rather than swallowed: the escalation ladder must be able to fall
            # through to the next rung, and it can only do that if this fails loudly.
            log.warning(
                "whatsapp_send_failed",
                status=response.status_code,
                error=str(data.get("error", {}).get("message"))[:200],
            )
            raise RuntimeError(f"WhatsApp rejected the template message: {response.status_code}")
        return data
