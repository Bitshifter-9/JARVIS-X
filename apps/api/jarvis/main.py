"""FastAPI application factory.

One container, three entrypoints (PLAN.md §6): this module is the ``api`` one. The
``worker`` and ``scheduler`` entrypoints import the same package and share the same
services — the boundaries are in the code, not in the deployment.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from jarvis.api.routes import (
    alexa,
    approvals,
    artifacts,
    auth,
    chat,
    connectors,
    devices,
    goals,
    health,
    hud,
    identities,
    insights,
    internal,
    me,
    memories,
    metrics,
    notifications,
    oauth,
    permissions,
    personas,
    proactivity,
    profile,
    routines,
    runs,
    webhooks,
    youtube,
)
from jarvis.api.routes import (
    settings as settings_routes,
)
from jarvis.api.routes import (
    system as system_routes,
)
from jarvis.api.routes import (
    timeline as timeline_routes,
)
from jarvis.api.routes import (
    tts as tts_routes,
)
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

    # An empty signing key is fatal everywhere. .env.example ships JARVIS_JWT_SECRET
    # blank, and a blank value in .env overrides the code default — which used to surface
    # as a 500 on the first login rather than as a startup failure.
    if not settings.jwt_secret.strip():
        raise RuntimeError(
            "JARVIS_JWT_SECRET is empty. Generate one and put it in .env:\n"
            '    echo "JARVIS_JWT_SECRET=$(openssl rand -hex 32)" >> .env'
        )

    # A placeholder key outside local development would let anyone mint a token for any
    # account. Refuse to start rather than serve traffic that looks authenticated.
    if settings.env not in ("local", "test") and settings.jwt_secret == INSECURE_JWT_DEFAULT:
        raise RuntimeError(
            "JARVIS_JWT_SECRET is still the development placeholder. "
            "Generate one with: openssl rand -hex 32"
        )

    # Settings the owner saved from the app, laid over the environment.
    try:
        from jarvis.core.overrides import apply_overrides
        from jarvis.db.session import session_scope

        async with session_scope() as session:
            applied = await apply_overrides(session)
        if applied:
            log.info("settings_overrides_applied", count=applied)
    except Exception as exc:  # noqa: BLE001 — a missing table on first boot is not fatal
        log.warning("settings_overrides_skipped", error=str(exc)[:120])

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

    # The Flutter web build is a development surface and runs on a different origin.
    # Never enabled outside local/test: a wildcard origin in production would let any
    # page a user visits drive their agent.
    if settings.env in ("local", "test"):
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=False,
            allow_methods=["*"],
            allow_headers=["*"],
            expose_headers=["X-Correlation-ID"],
        )

    app.add_middleware(CorrelationMiddleware)
    # Timeline and HUD payloads shrink 5–10× on the wire; phones on mobile data notice.
    app.add_middleware(GZipMiddleware, minimum_size=1000)
    register_exception_handlers(app)

    app.include_router(health.router)
    app.include_router(alexa.router)
    app.include_router(auth.router)
    app.include_router(oauth.router)
    app.include_router(goals.router)
    app.include_router(approvals.router)
    app.include_router(devices.router)
    app.include_router(webhooks.router)
    app.include_router(internal.router)
    app.include_router(identities.router)
    app.include_router(notifications.router)
    app.include_router(memories.router)
    app.include_router(runs.router)
    app.include_router(profile.router)
    app.include_router(system_routes.router)
    app.include_router(metrics.router)
    app.include_router(connectors.router)
    app.include_router(youtube.router)
    app.include_router(settings_routes.router)
    app.include_router(chat.router)
    app.include_router(artifacts.router)
    app.include_router(timeline_routes.router)
    app.include_router(tts_routes.router)
    app.include_router(hud.router)
    app.include_router(routines.router)
    app.include_router(me.router)
    app.include_router(personas.router)
    app.include_router(insights.router)
    app.include_router(proactivity.router)
    app.include_router(permissions.router)

    return app


app = create_app()
