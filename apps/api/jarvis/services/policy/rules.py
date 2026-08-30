"""Risk rules as data.

The policy engine lives **outside** the agent (blueprint §9): the model proposes, this
decides, and the executor revalidates independently. Keeping the rules as a table rather
than as branches means they can be reviewed by someone who does not read Python control
flow, and every rule ships with test vectors in ``tests/unit/test_policy.py``.

``POLICY_VERSION`` is stamped onto every dispatched action, so an audit entry can always
answer *"which rules were in force when this ran?"*
"""

from __future__ import annotations

from dataclasses import dataclass, field

from jarvis.db.models.agent import Risk

POLICY_VERSION = 1


@dataclass(frozen=True)
class ToolRule:
    tool: str
    risk: Risk
    description: str
    # Conditions the executor checks against the args, not just against the tool name.
    conditions: tuple[str, ...] = ()
    # R3 needs a second, local confirmation on the Mac itself.
    requires_local_confirmation: bool = False
    # May a standing permission pre-authorize this? Never for R3/R4.
    standing_permission_allowed: bool = False
    repairs: tuple[str, ...] = ()
    max_attempts: int = 1


# ── the ladder (blueprint §9) ──────────────────────────────────────────
# R0 read-only          automatic after connector consent
# R1 reversible local   automatic on the paired owner device
# R2 external effect    preview + single approval
# R3 destructive        approval + local Mac confirmation
# R4 prohibited         denied, unconditionally
RULES: dict[str, ToolRule] = {
    r.tool: r
    for r in (
        # ── R0 ─────────────────────────────────────────────────────────
        ToolRule("tasks.list", Risk.R0, "List the user's tasks"),
        ToolRule("tasks.get", Risk.R0, "Read one task"),
        ToolRule("goals.predict", Risk.R0, "Compute a failure prediction"),
        ToolRule("memory.search", Risk.R0, "Search semantic memory"),
        ToolRule("mail.read_metadata", Risk.R0, "Read message headers, not bodies"),
        ToolRule("browser.read", Risk.R0, "Fetch and read a page, no interaction"),
        # ── R1 ─────────────────────────────────────────────────────────
        ToolRule(
            "mac.open_app", Risk.R1, "Open an allowlisted application",
            conditions=("device_is_paired_owner", "bundle_id_in_allowlist"),
            standing_permission_allowed=True, repairs=("mac.focus_app",), max_attempts=2,
        ),
        ToolRule(
            "mac.focus_app", Risk.R1, "Bring an allowlisted application forward",
            conditions=("device_is_paired_owner", "bundle_id_in_allowlist"),
            standing_permission_allowed=True,
        ),
        ToolRule(
            "browser.navigate", Risk.R1, "Navigate the automation browser to a URL",
            conditions=("url_scheme_is_https",), standing_permission_allowed=True, max_attempts=2,
        ),
        ToolRule("focus.start", Risk.R1, "Start a focus session", standing_permission_allowed=True),
        ToolRule(
            "gmail.create_draft", Risk.R1, "Create a draft; nothing is sent",
            standing_permission_allowed=True,
        ),
        ToolRule(
            "mac.read_ui", Risk.R1, "Read an allowlisted app's accessibility tree",
            conditions=("device_is_paired_owner", "bundle_id_in_allowlist"),
        ),
        ToolRule(
            "mac.file_exists", Risk.R1, "Check a file inside a user-selected directory",
            conditions=("device_is_paired_owner", "path_in_scoped_directory"),
        ),
        # Capturing the screen is R2, not R1: the effect leaves the machine as evidence,
        # and whatever was on screen leaves with it.
        ToolRule(
            "mac.capture_window", Risk.R2, "Capture one window of an allowlisted app",
            conditions=("device_is_paired_owner", "bundle_id_in_allowlist"),
        ),
        # Pressing a button drives another application. What that button does is not
        # knowable from here, so it is never automatic.
        ToolRule(
            "mac.press_button", Risk.R2, "Press a named control in an allowlisted app",
            conditions=("device_is_paired_owner", "bundle_id_in_allowlist"),
        ),
        # ── R2 ─────────────────────────────────────────────────────────
        ToolRule("message.send", Risk.R2, "Send a message to another person"),
        ToolRule("gmail.send", Risk.R2, "Send an email"),
        ToolRule("slack.post_message", Risk.R2, "Post to a Slack channel"),
        ToolRule("calendar.create_event", Risk.R2, "Create a calendar invite"),
        ToolRule("browser.submit_form", Risk.R2, "Submit a form on a live page"),
        # ── R3 ─────────────────────────────────────────────────────────
        ToolRule(
            "mac.run_template", Risk.R3, "Run a predefined command template",
            conditions=("template_is_registered",), requires_local_confirmation=True,
        ),
        ToolRule(
            "files.delete", Risk.R3, "Delete a file in a user-selected directory",
            conditions=("path_in_scoped_directory",), requires_local_confirmation=True,
        ),
        ToolRule(
            "connector.change_permissions", Risk.R3, "Change a connector's scopes",
            requires_local_confirmation=True,
        ),
        # ── R4: denied unconditionally ─────────────────────────────────
        ToolRule("payment.send", Risk.R4, "Move money"),
        ToolRule("credentials.export", Risk.R4, "Export secrets"),
        ToolRule("audit.disable", Risk.R4, "Disable the audit log"),
        ToolRule("shell.execute", Risk.R4, "Run an arbitrary shell command"),
    )
}

# Default when a tool is unknown to the registry. An unregistered tool is not a
# convenience to be granted — it is a gap in review.
UNKNOWN_TOOL_RISK = Risk.R4


@dataclass(frozen=True)
class ToolManifest:
    """Execution contract for a tool (blueprint §5).

    ``verify`` is the important field: an action that declares no evidence cannot be
    verified, and an action that cannot be verified is not dispatchable.
    """

    tool: str
    timeout_seconds: int = 30
    verify: tuple[str, ...] = ()
    retry_on: tuple[str, ...] = ()
    requires_device: bool = False
    simulatable: bool = True
    args_required: tuple[str, ...] = ()
    repairs: tuple[str, ...] = field(default_factory=tuple)


MANIFESTS: dict[str, ToolManifest] = {
    m.tool: m
    for m in (
        ToolManifest(
            "mac.open_app", timeout_seconds=20,
            verify=("process_running", "foreground_window_bundle_id"),
            retry_on=("TRANSIENT_DEVICE_BUSY",), requires_device=True,
            args_required=("bundle_id",), repairs=("mac.focus_app",),
        ),
        ToolManifest(
            "mac.focus_app", timeout_seconds=10,
            verify=("foreground_window_bundle_id",), requires_device=True,
            args_required=("bundle_id",),
        ),
        ToolManifest(
            "browser.navigate", timeout_seconds=45,
            verify=("dom_url_matches",), retry_on=("TIMEOUT",), args_required=("url",),
        ),
        ToolManifest(
            "browser.read", timeout_seconds=45,
            verify=("dom_url_matches", "http_status"), args_required=("url",),
        ),
        ToolManifest(
            "browser.submit_form", timeout_seconds=60,
            verify=("dom_url_matches", "dom_selector_present"),
            args_required=("url", "selector"),
        ),
        ToolManifest(
            "message.send", timeout_seconds=30,
            verify=("provider_object_id",), args_required=("channel", "to", "body"),
        ),
        ToolManifest(
            "gmail.send", timeout_seconds=30,
            verify=("provider_object_id",), args_required=("to", "subject", "body"),
        ),
        ToolManifest(
            "gmail.create_draft", timeout_seconds=30,
            verify=("provider_object_id",), args_required=("to", "body"),
        ),
        ToolManifest(
            "slack.post_message", timeout_seconds=30,
            verify=("provider_object_id",), args_required=("channel", "text"),
        ),
        ToolManifest(
            "calendar.create_event", timeout_seconds=30,
            verify=("provider_object_id",), args_required=("title", "start", "end"),
        ),
        ToolManifest(
            "mac.run_template", timeout_seconds=60,
            verify=("http_status",), requires_device=True,
            simulatable=True, args_required=("template",),
        ),
        ToolManifest(
            "mac.read_ui", timeout_seconds=15,
            verify=("process_running",), requires_device=True, args_required=("bundle_id",),
        ),
        ToolManifest(
            "mac.press_button", timeout_seconds=20,
            verify=("foreground_window_bundle_id",), requires_device=True,
            args_required=("bundle_id", "title"),
        ),
        ToolManifest(
            "mac.capture_window", timeout_seconds=20,
            verify=("screenshot",), requires_device=True, args_required=("bundle_id",),
        ),
        ToolManifest(
            "mac.file_exists", timeout_seconds=10,
            verify=("file_exists",), requires_device=True,
            args_required=("path", "scope_bookmark"),
        ),
        ToolManifest("focus.start", timeout_seconds=10, verify=("process_running",)),
        ToolManifest("tasks.list", timeout_seconds=10, verify=("http_status",)),
        ToolManifest("goals.predict", timeout_seconds=15, verify=("http_status",)),
        ToolManifest("memory.search", timeout_seconds=15, verify=("http_status",)),
    )
}


# Which argument supplies the expected value for each evidence kind. A manifest declares
# *which* kinds of evidence an action must produce; this binds them to the *specific*
# value this particular action must produce. Without it a check like ``dom_url_matches``
# has nothing to compare against, and an unbound check proves nothing.
EVIDENCE_BINDINGS: dict[str, str] = {
    "dom_url_matches": "url",
    "foreground_window_bundle_id": "bundle_id",
    "dom_selector_present": "selector",
    "window_title_matches": "expect_title",
    "file_exists": "path",
}


def bind_expected(kinds: tuple[str, ...], args: dict) -> list[dict]:
    """Turn a manifest's evidence kinds into this action's concrete expectations."""
    bound: list[dict] = []
    for kind in kinds:
        arg_name = EVIDENCE_BINDINGS.get(kind)
        entry: dict = {"kind": kind}
        if arg_name is not None and arg_name in args:
            entry["value"] = args[arg_name]
        bound.append(entry)
    return bound


def rule_for(tool: str) -> ToolRule:
    """The rule for a tool. An unregistered tool is R4 — denied, not waved through."""
    return RULES.get(
        tool,
        ToolRule(tool, UNKNOWN_TOOL_RISK, "Unregistered tool"),
    )


def manifest_for(tool: str) -> ToolManifest | None:
    return MANIFESTS.get(tool)
