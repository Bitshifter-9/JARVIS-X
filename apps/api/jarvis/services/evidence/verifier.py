"""Verification: comparing what was required against what was observed.

The rule the whole architecture rests on (blueprint §2): **a zero exit code is not
evidence, and model confidence is never evidence.** A tool succeeded when the world is
observably in the expected state, and not before.

Pure functions here; persistence lives in ``service.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from jarvis.db.models.agent import Verdict


@dataclass(frozen=True)
class EvidenceRequirement:
    kind: str
    value: Any = None


@dataclass(frozen=True)
class CheckResult:
    kind: str
    verdict: Verdict
    expected: Any
    observed: Any
    detail: str = ""

    @property
    def passed(self) -> bool:
        return self.verdict is Verdict.VERIFIED


def check_requirement(
    requirement: EvidenceRequirement, observed: dict[str, Any]
) -> CheckResult:
    """Check one requirement against observed state.

    A requirement whose observation is simply *absent* is ``INCONCLUSIVE``, not
    ``FAILED``. The distinction matters: the agent's reflect step retries on new
    evidence and stops on none, so conflating "I could not see" with "it did not work"
    would make it give up exactly when it should look again.
    """
    kind = requirement.kind
    expected = requirement.value

    match kind:
        case "process_running":
            pid = observed.get("pid")
            running = observed.get("is_running")
            if pid is None and running is None:
                return CheckResult(kind, Verdict.INCONCLUSIVE, expected, None, "no process data")
            ok = bool(running) if running is not None else bool(pid)
            return CheckResult(
                kind,
                Verdict.VERIFIED if ok else Verdict.FAILED,
                expected, {"pid": pid, "is_running": running},
                "process observed running" if ok else "process not running",
            )

        case "foreground_window_bundle_id":
            actual = observed.get("frontmost_bundle_id")
            if actual is None:
                return CheckResult(kind, Verdict.INCONCLUSIVE, expected, None, "no window data")
            if expected is None:
                return CheckResult(
                    kind, Verdict.INCONCLUSIVE, None, actual, "no expected bundle id was bound"
                )
            ok = actual == expected
            return CheckResult(
                kind, Verdict.VERIFIED if ok else Verdict.FAILED, expected, actual,
                "expected app is frontmost" if ok else f"{actual} is frontmost instead",
            )

        case "window_title_matches":
            actual = observed.get("window_title")
            if actual is None:
                return CheckResult(kind, Verdict.INCONCLUSIVE, expected, None, "no title")
            if expected is None:
                return CheckResult(
                    kind, Verdict.INCONCLUSIVE, None, actual, "no expected title was bound"
                )
            ok = str(expected).lower() in str(actual).lower()
            return CheckResult(kind, Verdict.VERIFIED if ok else Verdict.FAILED, expected, actual)

        case "dom_url_matches":
            actual = observed.get("url")
            if actual is None:
                return CheckResult(kind, Verdict.INCONCLUSIVE, expected, None, "no URL observed")
            if expected is None:
                # No target to compare against proves nothing. Treating this as a pass
                # would rubber-stamp a redirect to a login wall.
                return CheckResult(
                    kind, Verdict.INCONCLUSIVE, None, actual, "no expected URL was bound"
                )
            ok = _urls_equivalent(str(expected), str(actual))
            return CheckResult(
                kind, Verdict.VERIFIED if ok else Verdict.FAILED, expected, actual,
                "landed on the expected page" if ok else "redirected elsewhere",
            )

        case "dom_selector_present":
            present = observed.get("selector_present")
            if present is None:
                return CheckResult(kind, Verdict.INCONCLUSIVE, expected, None, "not inspected")
            return CheckResult(
                kind, Verdict.VERIFIED if present else Verdict.FAILED, expected, present
            )

        case "http_status":
            status = observed.get("status")
            if status is None:
                return CheckResult(kind, Verdict.INCONCLUSIVE, expected, None, "no status")
            ok = (200 <= int(status) < 400) if expected is None else int(status) == int(expected)
            return CheckResult(kind, Verdict.VERIFIED if ok else Verdict.FAILED, expected, status)

        case "provider_object_id":
            # The provider's own id for the thing it created. This is the difference
            # between "the API returned 200" and "the message exists".
            actual = observed.get("provider_object_id")
            if not actual:
                return CheckResult(
                    kind, Verdict.FAILED, expected, None,
                    "the provider returned no object id, so nothing is known to exist",
                )
            return CheckResult(kind, Verdict.VERIFIED, expected, actual)

        case "file_exists":
            exists = observed.get("exists")
            if exists is None:
                return CheckResult(kind, Verdict.INCONCLUSIVE, expected, None, "not checked")
            return CheckResult(kind, Verdict.VERIFIED if exists else Verdict.FAILED, expected, exists)

        case "screenshot":
            digest = observed.get("digest")
            return CheckResult(
                kind,
                Verdict.VERIFIED if digest else Verdict.INCONCLUSIVE,
                expected, digest,
                "screenshot captured" if digest else "no screenshot",
            )

        case _:
            # An unknown requirement cannot be satisfied. Failing closed here means a
            # typo in a manifest blocks the action rather than silently approving it.
            return CheckResult(
                kind, Verdict.FAILED, expected, None, f"unknown evidence kind: {kind}"
            )


def _urls_equivalent(expected: str, actual: str) -> bool:
    """Compare URLs ignoring differences that do not change the page.

    Trailing slashes and a ``www.`` prefix are not a different destination; a different
    host or path is.
    """
    a, b = urlparse(expected), urlparse(actual)
    host_a = a.netloc.lower().removeprefix("www.")
    host_b = b.netloc.lower().removeprefix("www.")
    path_a = a.path.rstrip("/") or "/"
    path_b = b.path.rstrip("/") or "/"
    return host_a == host_b and path_a == path_b


class Verifier:
    """Combines requirement checks into a single verdict for an action."""

    @staticmethod
    def check_all(
        requirements: list[EvidenceRequirement], observed: dict[str, Any]
    ) -> list[CheckResult]:
        return [check_requirement(r, observed) for r in requirements]

    @staticmethod
    def overall(results: list[CheckResult]) -> Verdict:
        """One failure fails the action; anything unproven leaves it inconclusive.

        Requiring *every* check to pass is deliberate. An action that satisfied three of
        four requirements has not done what it said it would.
        """
        if not results:
            return Verdict.INCONCLUSIVE
        if any(r.verdict is Verdict.FAILED for r in results):
            return Verdict.FAILED
        if any(r.verdict is Verdict.INCONCLUSIVE for r in results):
            return Verdict.INCONCLUSIVE
        return Verdict.VERIFIED
