"""FastAPI application factory.

One container, three entrypoints (PLAN.md §6): this module is the ``api`` one. The
``worker`` and ``scheduler`` entrypoints import the same package and share the same
services — the boundaries are in the code, not in the deployment.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from jarvis.api.routes import approvals, auth, devices, goals, health, oauth, webhooks
from jarvis.core.config import get_settings
from jarvis.core.correlation import CorrelationMiddleware
from jarvis.core.errors import register_exception_handlers
from jarvis.core.logging import configure_logging, get_logger
from jarvis.db.session import dispose_engine

log = get_logger(__name__)

INSECURE_JWT_DEFAULT = "dev-only-insecure-secret-change-me"  # noqa: S105


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings = get_settings()
    configure_logging(level=settings.log_level, json_output=settings.env != "local")

    # A placeholder signing key outside local development would let anyone mint a token
    # for any account. Refuse to start rather than serve traffic that looks authenticated.
    if settings.env not in ("local", "test") and settings.jwt_secret == INSECURE_JWT_DEFAULT:
        raise RuntimeError(
            "JARVIS_JWT_SECRET is still the development placeholder. "
            "Generate one with: openssl rand -hex 32"
        )

    log.info(
        "api_starting",
        env=settings.env,
        paid_llm=settings.enable_paid_llm,
        global_pause=settings.global_pause,
    )
    yield
    await dispose_engine()
    log.info("api_stopped")


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="JARVIS X",
        description=(
            "Autonomous personal operations platform. Observes commitments, predicts "
            "failure, prepares the next best action, executes through policy-controlled "
            "tools, verifies the result, and escalates only when authorized."
        ),
        version="0.1.0",
        lifespan=lifespan,
        # Docs are for us, not for the internet.
        docs_url="/docs" if settings.env in ("local", "test") else None,
        redoc_url=None,
    )

    app.add_middleware(CorrelationMiddleware)
    register_exception_handlers(app)

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(oauth.router)
    app.include_router(goals.router)
    app.include_router(approvals.router)
    app.include_router(devices.router)
    app.include_router(webhooks.router)

    return app


app = create_app()
