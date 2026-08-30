"""Shared test fixtures.

Integration tests run against a real PostgreSQL, never a SQLite stand-in: the queue's
correctness rests on ``FOR UPDATE SKIP LOCKED``, which SQLite does not have. A test that
cannot exercise the mechanism cannot prove it.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import text

# Point every import at the test database before application config is first read.
os.environ.setdefault(
    "JARVIS_DATABASE_URL", "postgresql+asyncpg://jarvis:jarvis@localhost:5433/jarvis_test"
)
os.environ.setdefault("JARVIS_ENV", "test")
os.environ.setdefault("JARVIS_JWT_SECRET", "test-secret-not-used-outside-tests")

import jarvis.db.models  # noqa: F401,E402  — registers models on Base.metadata
from jarvis.core.logging import configure_logging  # noqa: E402
from jarvis.db.base import Base  # noqa: E402
from jarvis.db.session import dispose_engine, get_engine, get_sessionmaker  # noqa: E402

configure_logging(level="WARNING", json_output=False)


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="session", autouse=True)
async def _schema():
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS vector")
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
    await dispose_engine()


@pytest.fixture(autouse=True)
async def _clean_tables(_schema):
    """Truncate between tests.

    Not a per-test transaction: the concurrency tests need genuinely separate sessions
    committing against each other, which a shared outer transaction would serialize away.
    """
    async with get_sessionmaker()() as session:
        # LangGraph's checkpoint tables are created by its own migrations and are not in
        # Base.metadata, so they are discovered rather than listed. Leaving them behind
        # leaks run state between tests.
        rows = await session.execute(
            text("""
                SELECT tablename FROM pg_tables
                WHERE schemaname = 'public' AND tablename <> 'alembic_version'
            """)
        )
        names = ", ".join(f'"{r[0]}"' for r in rows)
        if names:
            await session.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
        await session.commit()
    yield


@pytest.fixture
async def session():
    async with get_sessionmaker()() as s:
        yield s
        await s.rollback()


@pytest.fixture
async def client():
    """An HTTP client bound to the ASGI app, with no network in between."""
    import httpx
    from jarvis.main import create_app

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture(scope="session")
def fixture_site():
    """A tiny local site for the browser worker to act on.

    Real HTTP and a real browser: the point of the 1.7 gate is that DOM evidence is
    genuinely observed, and a mocked page would prove nothing.
    """
    import http.server
    import threading

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):  # keep the test output readable
            pass

        def _send(self, body: str, status: int = 200, headers: dict | None = None):
            encoded = body.encode()
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(encoded)

        def do_GET(self):  # noqa: N802
            if self.path.startswith("/deadline"):
                self._send(
                    "<html><head><title>CS401 Assignment 3</title></head><body>"
                    "<h1 id='title'>Assignment 3</h1>"
                    "<p class='due'>Due 5 September 2026, 23:59 IST</p>"
                    "</body></html>"
                )
            elif self.path.startswith("/redirect"):
                self._send("", status=302, headers={"Location": "/login"})
            elif self.path.startswith("/login"):
                self._send("<html><head><title>Sign in</title></head><body>Sign in</body></html>")
            elif self.path.startswith("/form"):
                self._send(
                    "<html><head><title>Submit</title></head><body>"
                    "<form method='post' action='/submitted'>"
                    "<input name='comment' id='comment'>"
                    "<button id='go' type='submit'>Send</button></form></body></html>"
                )
            elif self.path.startswith("/submitted"):
                self._send(
                    "<html><head><title>Received</title></head><body>"
                    "<div id='receipt'>Received</div></body></html>"
                )
            elif self.path.startswith("/missing"):
                self._send("<html><body>gone</body></html>", status=404)
            else:
                self._send("<html><head><title>Home</title></head><body>Home</body></html>")

        def do_POST(self):  # noqa: N802
            self._send(
                "<html><head><title>Received</title></head><body>"
                "<div id='receipt'>Received</div></body></html>"
            )

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def pytest_addoption(parser):
    parser.addoption(
        "--live-eval",
        action="store_true",
        default=False,
        help="run evals against real LLM providers (needs API keys, spends quota)",
    )


@pytest.fixture
def live_eval(request):
    if not request.config.getoption("--live-eval"):
        pytest.skip("live eval: pass --live-eval to run against real providers")
    return True
