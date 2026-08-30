"""JARVIS X Mac node — the optional local execution helper.

Optional by design (PLAN.md §3). The backend never depends on it: with the Mac offline,
ingestion, prediction, scheduling, approvals and cloud browser automation all continue.
This helper adds exactly one thing — actions that can only happen on a Mac.
"""

__version__ = "0.1.0"
