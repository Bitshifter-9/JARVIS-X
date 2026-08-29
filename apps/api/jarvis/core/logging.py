"""Structured JSON logging.

Every line carries the correlation id, so a single request is greppable end to end.
Secrets are redacted at render time rather than at call sites, because a call site that
must remember to redact will eventually forget.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

from jarvis.core.correlation import get_correlation_id

# Substring match against the *key*, lowercased. Deliberately broad.
_SENSITIVE_KEYS = (
    "password", "secret", "token", "api_key", "apikey", "authorization",
    "refresh_token", "access_token", "client_secret", "code_verifier",
    "private_key", "signature", "cookie",
)
REDACTED = "«redacted»"


def _redact(_logger: Any, _name: str, event_dict: dict) -> dict:
    for key in list(event_dict):
        if any(s in key.lower() for s in _SENSITIVE_KEYS):
            event_dict[key] = REDACTED
    return event_dict


def _add_correlation(_logger: Any, _name: str, event_dict: dict) -> dict:
    cid = get_correlation_id()
    if cid:
        event_dict["correlation_id"] = cid
    return event_dict


def configure_logging(level: str = "INFO", json_output: bool = True) -> None:
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level.upper())

    renderer = (
        structlog.processors.JSONRenderer()
        if json_output
        else structlog.dev.ConsoleRenderer(colors=True)
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            _add_correlation,
            _redact,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelName(level.upper())
        ),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
