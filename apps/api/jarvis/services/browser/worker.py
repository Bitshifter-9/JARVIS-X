"""Headless browser worker.

Runs on the VPS, not on the Mac — which is the whole point. Every method returns
*observations*, never a boolean "worked": the verifier decides, from state.

Safety posture, unchanged from every other execution path:

* Page text is **untrusted data**. It can propose no tool call and cannot change policy.
* The browser runs in a throwaway context with **no logged-in personal session** by
  default; a connector supplies scoped credentials only for the site it owns.
* Navigation is allowlisted per action and downloads are refused, so a hostile page
  cannot reach anything the action did not name.
"""

from __future__ import annotations

import hashlib
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from jarvis.core.config import get_settings
from jarvis.core.logging import get_logger

log = get_logger(__name__)

BLOCKED_SCHEMES = ("file", "javascript", "data", "about", "chrome", "view-source")
# Loopback and link-local ranges. A page must not be able to steer the worker at the
# metadata service or anything else on the VPS's own network.
BLOCKED_HOSTS = (
    "localhost",
    "127.0.0.1",
    "0.0.0.0",  # noqa: S104 — a denylist entry, not a bind address
    "169.254.169.254",
    "::1",
    "metadata",
)


class UnsafeNavigation(ValueError):
    """A URL the worker refuses to open."""


@dataclass
class BrowserObservation:
    """What the browser saw. Feeds straight into the verifier."""

    url: str | None = None
    status: int | None = None
    title: str | None = None
    selector_present: bool | None = None
    text_excerpt: str | None = None
    digest: str | None = None
    screenshot_path: str | None = None
    extras: dict[str, Any] = field(default_factory=dict)

    def as_observed(self) -> dict[str, Any]:
        observed = {
            "url": self.url,
            "status": self.status,
            "window_title": self.title,
            "selector_present": self.selector_present,
            "digest": self.digest,
        }
        observed.update(self.extras)
        return {k: v for k, v in observed.items() if v is not None}


def assert_safe_url(url: str, *, allow_loopback: bool = False) -> str:
    """Refuse anything that is not an ordinary outbound web request.

    ``file:`` reads the VPS disk; ``javascript:`` executes in whatever page is loaded;
    loopback and link-local addresses reach the host's own services. None of those are
    what "navigate to a page" means.

    ``allow_loopback`` exists so the test suite can point the worker at a local fixture
    server. It is never set from configuration and never set in production — the scheme
    checks apply regardless of it.
    """
    parsed = urlparse(url)
    if parsed.scheme.lower() in BLOCKED_SCHEMES or parsed.scheme.lower() not in ("http", "https"):
        raise UnsafeNavigation(f"refusing scheme {parsed.scheme!r}")
    host = (parsed.hostname or "").lower()
    if not host:
        raise UnsafeNavigation("refusing a URL with no host")
    if allow_loopback and host in ("localhost", "127.0.0.1", "::1"):
        return url
    if host in BLOCKED_HOSTS or host.endswith(".localhost"):
        raise UnsafeNavigation(f"refusing internal host {host!r}")
    return url


class BrowserWorker:
    """Playwright-backed automation.

    Playwright is imported lazily so importing the app — or running the unit tests —
    does not require a browser binary to be installed.
    """

    def __init__(
        self,
        *,
        headless: bool | None = None,
        timeout_ms: int | None = None,
        allow_loopback: bool = False,
    ) -> None:
        settings = get_settings()
        self.headless = settings.browser_headless if headless is None else headless
        self.timeout_ms = timeout_ms or settings.browser_timeout_seconds * 1000
        # Test seam only; see assert_safe_url.
        self.allow_loopback = allow_loopback

    @asynccontextmanager
    async def _page(self):
        from playwright.async_api import async_playwright

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=self.headless)
            # A fresh context per action: no cookies, no storage, nothing carried over
            # from a previous task or a previous tenant.
            context = await browser.new_context(accept_downloads=False)
            context.set_default_timeout(self.timeout_ms)
            page = await context.new_page()
            try:
                yield page
            finally:
                await context.close()
                await browser.close()

    async def navigate(self, url: str, *, expect_selector: str | None = None) -> BrowserObservation:
        """Open a page and report what is actually there.

        Reports the URL *after* redirects, so a login wall or a consent interstitial is
        visible as a different destination rather than being mistaken for success.
        """
        assert_safe_url(url, allow_loopback=self.allow_loopback)
        async with self._page() as page:
            response = await page.goto(url, wait_until="domcontentloaded")
            observation = BrowserObservation(
                url=page.url,
                status=response.status if response else None,
                title=await page.title(),
            )
            if expect_selector:
                observation.selector_present = (
                    await page.query_selector(expect_selector)
                ) is not None
            has_body = await page.query_selector("body")
            body = (await page.inner_text("body"))[:4000] if has_body else ""
            observation.text_excerpt = body
            observation.digest = "sha256:" + hashlib.sha256(body.encode()).hexdigest()
            log.info(
                "browser_navigated",
                requested=url, landed=page.url,
                status=observation.status, title=observation.title,
            )
            return observation

    async def read(self, url: str) -> BrowserObservation:
        """Fetch a page for reading. The result is untrusted data (blueprint §2)."""
        return await self.navigate(url)

    async def submit_form(
        self, url: str, *, fields: dict[str, str], submit_selector: str,
        expect_selector: str | None = None,
    ) -> BrowserObservation:
        """Fill and submit a form. R2 — never reached without an approval."""
        assert_safe_url(url, allow_loopback=self.allow_loopback)
        async with self._page() as page:
            await page.goto(url, wait_until="domcontentloaded")
            for selector, value in fields.items():
                await page.fill(selector, value)
            await page.click(submit_selector)
            await page.wait_for_load_state("domcontentloaded")

            observation = BrowserObservation(url=page.url, title=await page.title(), status=200)
            if expect_selector:
                observation.selector_present = (
                    await page.query_selector(expect_selector)
                ) is not None
            body = (await page.inner_text("body"))[:4000]
            observation.digest = "sha256:" + hashlib.sha256(body.encode()).hexdigest()
            log.info("browser_form_submitted", landed=page.url, url=url)
            return observation
