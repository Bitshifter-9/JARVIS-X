"""Cloud-side browser automation.

This is what keeps "execute, then verify with real DOM evidence" alive when the Mac is
offline (PLAN.md §3). Headless Playwright runs on the VPS, in the worker entrypoint.
"""

from jarvis.services.browser.worker import BrowserObservation, BrowserWorker

__all__ = ["BrowserObservation", "BrowserWorker"]
