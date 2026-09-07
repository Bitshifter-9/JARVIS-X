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
        ToolRule("weather.now", Risk.R0, "Current weather and today's range for a place"),
        ToolRule("news.headlines", Risk.R0, "Recent headlines for a topic (topic)"),
        ToolRule(
            "activity.query", Risk.R0,
            "Minutes per app the owner spent in a window (start, end: ISO datetimes)",
        ),
        ToolRule(
            "browser.act", Risk.R1,
            "Browse on your own to answer a goal (goal, url, allowed_domains?): follow links, "
            "read pages, fill fields; never submits a form — that is browser.submit_form",
        ),
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
            "docs.create", Risk.R1,
            "Create a document from text you composed (title, content, format: md|txt|csv|pdf) "
            "and deliver it as a file",
        ),
        ToolRule(
            "gmail.create_draft", Risk.R1,
            "Create a draft in a Gmail account (from_account); nothing is sent",
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
        # ── the Mac as a hand, not a shell (every one is a typed verb) ──
        ToolRule(
            "mac.open_url", Risk.R1, "Open a URL in its default app on the Mac",
            conditions=("device_is_paired_owner", "url_scheme_allowlisted"),
            standing_permission_allowed=True,
        ),
        ToolRule(
            "mac.open_file", Risk.R1, "Open a file inside a user-selected directory",
            conditions=("device_is_paired_owner", "path_in_scoped_directory"),
        ),
        ToolRule(
            "mac.find_files", Risk.R1, "Search files inside a user-selected directory",
            conditions=("device_is_paired_owner", "path_in_scoped_directory"),
        ),
        ToolRule(
            "mac.clipboard_read", Risk.R1, "Read the Mac clipboard",
            conditions=("device_is_paired_owner",),
        ),
        ToolRule(
            "mac.clipboard_write", Risk.R1, "Put text on the Mac clipboard",
            conditions=("device_is_paired_owner",),
        ),
        ToolRule(
            "mac.notify", Risk.R1, "Show a notification on the Mac",
            conditions=("device_is_paired_owner",), standing_permission_allowed=True,
        ),
        ToolRule(
            "mac.system_info", Risk.R1, "Battery, load, disk and uptime of the Mac",
            conditions=("device_is_paired_owner",), standing_permission_allowed=True,
        ),
        ToolRule(
            "mac.ring", Risk.R1, "Ring the Mac loudly to find it",
            conditions=("device_is_paired_owner",), standing_permission_allowed=True,
        ),
        ToolRule(
            "mac.say", Risk.R1, "Speak a sentence aloud on the Mac",
            conditions=("device_is_paired_owner",), standing_permission_allowed=True,
        ),
        ToolRule(
            "mac.lock_screen", Risk.R1, "Lock the Mac's screen",
            conditions=("device_is_paired_owner",), standing_permission_allowed=True,
        ),
        ToolRule(
            "mac.set_volume", Risk.R1, "Set the Mac's output volume",
            conditions=("device_is_paired_owner",), standing_permission_allowed=True,
        ),
        ToolRule(
            "mac.media", Risk.R1, "Play, pause or skip in Music or Spotify",
            conditions=("device_is_paired_owner",), standing_permission_allowed=True,
        ),
        # Typing and key presses drive whatever app is in front. What that keystroke
        # does is not knowable from here, so neither is ever automatic.
        ToolRule(
            "mac.type_text", Risk.R2, "Type text into an allowlisted app",
            conditions=("device_is_paired_owner", "bundle_id_in_allowlist"),
            standing_permission_allowed=True,
        ),
        ToolRule(
            "mac.press_key", Risk.R2, "Press a key combination in an allowlisted app",
            conditions=("device_is_paired_owner", "bundle_id_in_allowlist"),
            standing_permission_allowed=True,
        ),
        # Pixels and files leave the machine: R2, like capture_window.
        ToolRule(
            "mac.capture_screen", Risk.R2, "Capture the whole screen and send it to you",
            standing_permission_allowed=True,
            conditions=("device_is_paired_owner",),
        ),
        ToolRule(
            "mac.describe_screen", Risk.R2,
            "Capture the Mac's screen and describe it (question: what to look for)",
            conditions=("device_is_paired_owner",), standing_permission_allowed=True,
        ),
        ToolRule(
            "phone.camera", Risk.R2,
            "Take one photo with the phone's camera and describe it (question)",
            conditions=("device_is_paired_owner",), standing_permission_allowed=True,
        ),
        ToolRule(
            "mac.send_file", Risk.R2, "Send a file from a user-selected directory to you",
            conditions=("device_is_paired_owner", "path_in_scoped_directory"),
            standing_permission_allowed=True,
        ),
        ToolRule(
            "mac.whatsapp_send", Risk.R2, "Send a WhatsApp message from the Mac",
            conditions=("device_is_paired_owner",), standing_permission_allowed=True,
        ),
        # A setting toggled on the Mac, re-read afterwards as the evidence (PLAN.md 10.4.1).
        ToolRule(
            "mac.set_setting", Risk.R1,
            "Change a Mac setting (key: wifi|bluetooth|dark_mode|do_not_disturb|brightness; "
            "value: on|off or 0-100)",
            conditions=("device_is_paired_owner", "setting_key_allowlisted"),
            standing_permission_allowed=True,
        ),
        # ── the phone as a hand ────────────────────────────────────────
        ToolRule(
            "phone.open_settings", Risk.R1,
            "Open a settings panel on the phone (panel: wifi|bluetooth|display|sound|battery|"
            "location|dnd|airplane|apps)",
            conditions=("device_is_paired_owner", "settings_panel_allowlisted"),
            standing_permission_allowed=True,
        ),
        # Places the call for real when the phone has the call permission; otherwise it
        # opens the dialer filled in. Either way the owner hears it ring.
        ToolRule(
            "phone.call", Risk.R2, "Place a call from the phone (number)",
            conditions=("device_is_paired_owner",), standing_permission_allowed=True,
        ),
        ToolRule(
            "phone.ring", Risk.R1, "Ring the phone loudly to find it, even in silent mode",
            conditions=("device_is_paired_owner",), standing_permission_allowed=True,
        ),
        ToolRule(
            "phone.system_info", Risk.R1, "Battery, storage and network of the phone",
            conditions=("device_is_paired_owner",), standing_permission_allowed=True,
        ),
        ToolRule(
            "phone.torch", Risk.R1, "Turn the phone's flashlight on or off (on: true/false)",
            conditions=("device_is_paired_owner",), standing_permission_allowed=True,
        ),
        ToolRule(
            "phone.locate", Risk.R2, "Read the phone's current location and send it to you",
            conditions=("device_is_paired_owner",), standing_permission_allowed=True,
        ),
        ToolRule(
            "phone.sms_draft", Risk.R1, "Open the SMS app with a number and text filled in",
            conditions=("device_is_paired_owner",), standing_permission_allowed=True,
        ),
        ToolRule(
            "phone.clipboard_read", Risk.R1, "Read the phone's clipboard",
            conditions=("device_is_paired_owner",), standing_permission_allowed=True,
        ),
        ToolRule(
            "phone.clipboard_write", Risk.R1, "Put text on the phone's clipboard",
            conditions=("device_is_paired_owner",), standing_permission_allowed=True,
        ),
        ToolRule(
            "phone.open_deeplink", Risk.R1,
            "Open an app link on the phone (maps, geo, spotify, youtube, upi, tel, mailto…)",
            conditions=("device_is_paired_owner", "deeplink_scheme_allowlisted"),
            standing_permission_allowed=True,
        ),
        ToolRule(
            "phone.open_url", Risk.R1, "Open a URL on the phone",
            conditions=("device_is_paired_owner", "url_scheme_allowlisted"),
            standing_permission_allowed=True,
        ),
        ToolRule(
            "phone.open_app", Risk.R1, "Open an allowlisted app on the phone",
            conditions=("device_is_paired_owner", "bundle_id_in_allowlist"),
            standing_permission_allowed=True,
        ),
        # Opens WhatsApp with the text filled in; the user taps send. Nothing leaves
        # the phone without a thumb, which is why it is R1 and not R2.
        ToolRule(
            "phone.whatsapp_draft", Risk.R1, "Draft a WhatsApp message on the phone",
            conditions=("device_is_paired_owner",), standing_permission_allowed=True,
        ),
        # Auto-sends via the phone's opt-in Accessibility Service (it taps Send). The
        # message leaves the phone on its own, so it is R2.
        ToolRule(
            "phone.whatsapp_send", Risk.R2, "Send a WhatsApp message from the phone (phone, text)",
            conditions=("device_is_paired_owner",), standing_permission_allowed=True,
        ),
        ToolRule(
            "phone.notify", Risk.R1, "Show a notification on the phone",
            conditions=("device_is_paired_owner",), standing_permission_allowed=True,
        ),
        # ── R2 ─────────────────────────────────────────────────────────
        ToolRule("message.send", Risk.R2, "Send a message to another person"),
        ToolRule("gmail.send", Risk.R2, "Send an email (from_account: which Gmail address)"),
        ToolRule("slack.post_message", Risk.R2, "Post to a Slack channel"),
        ToolRule(
            "youtube.upload", Risk.R2, "Upload a rendered video to YouTube",
            standing_permission_allowed=True,  # the content agent's envelope (10.9.2)
        ),
        ToolRule("youtube.reply", Risk.R2, "Post a reply to a YouTube comment"),
        ToolRule(
            "calendar.create_event", Risk.R2, "Create a calendar invite",
            standing_permission_allowed=True,
        ),
        ToolRule("browser.submit_form", Risk.R2, "Submit a form on a live page"),
        # ── R3 ─────────────────────────────────────────────────────────
        ToolRule(
            "mac.run_template", Risk.R3, "Run a predefined command template",
            conditions=("template_is_registered",), requires_local_confirmation=True,
        ),
        # Clicking by coordinate is blind; it exists only for apps with no typed verb.
        ToolRule(
            "mac.click", Risk.R3, "Click a screen coordinate on the Mac (x, y)",
            conditions=("device_is_paired_owner",), requires_local_confirmation=True,
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
            "browser.act", timeout_seconds=240,
            verify=("http_status",), args_required=("goal", "url"),
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
            "docs.create", timeout_seconds=90,
            verify=("artifact_uploaded",), args_required=("title", "content"),
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
            "youtube.upload", timeout_seconds=600,
            verify=("provider_object_id",), args_required=("file_path", "title"),
        ),
        ToolManifest(
            "youtube.reply", timeout_seconds=30,
            verify=("provider_object_id",), args_required=("parent_id", "text"),
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
        ToolManifest(
            "mac.open_url", timeout_seconds=20, verify=("url_opened",),
            requires_device=True, args_required=("url",),
        ),
        ToolManifest(
            "mac.open_file", timeout_seconds=20, verify=("http_status",),
            requires_device=True, args_required=("path", "scope_bookmark"),
        ),
        ToolManifest(
            "mac.find_files", timeout_seconds=30, verify=("http_status",),
            requires_device=True, args_required=("query", "scope_bookmark"),
        ),
        ToolManifest(
            "mac.clipboard_read", timeout_seconds=10, verify=("http_status",),
            requires_device=True,
        ),
        ToolManifest(
            "mac.clipboard_write", timeout_seconds=10, verify=("http_status",),
            requires_device=True, args_required=("text",),
        ),
        ToolManifest(
            "mac.notify", timeout_seconds=10, verify=("http_status",),
            requires_device=True, args_required=("title", "body"),
        ),
        ToolManifest(
            "mac.lock_screen", timeout_seconds=10, verify=("http_status",), requires_device=True,
        ),
        ToolManifest(
            "mac.set_volume", timeout_seconds=10, verify=("http_status",),
            requires_device=True, args_required=("level",),
        ),
        ToolManifest(
            "mac.media", timeout_seconds=10, verify=("http_status",),
            requires_device=True, args_required=("app", "command"),
        ),
        ToolManifest(
            "mac.type_text", timeout_seconds=30, verify=("foreground_window_bundle_id",),
            requires_device=True, args_required=("bundle_id", "text"),
        ),
        ToolManifest(
            "mac.press_key", timeout_seconds=15, verify=("foreground_window_bundle_id",),
            requires_device=True, args_required=("bundle_id", "key"),
        ),
        ToolManifest(
            "mac.capture_screen", timeout_seconds=30, verify=("screenshot", "artifact_uploaded"),
            requires_device=True,
        ),
        ToolManifest(
            "mac.send_file", timeout_seconds=120, verify=("artifact_uploaded",),
            requires_device=True, args_required=("path", "scope_bookmark"),
        ),
        ToolManifest(
            "mac.whatsapp_send", timeout_seconds=40, verify=("url_opened", "key_pressed"),
            requires_device=True, args_required=("phone", "text"),
        ),
        ToolManifest(
            "mac.describe_screen", timeout_seconds=30, verify=("artifact_uploaded",),
            requires_device=True,
        ),
        ToolManifest(
            "phone.camera", timeout_seconds=120, verify=("artifact_uploaded",),
            requires_device=True,
        ),
        ToolManifest(
            "mac.click", timeout_seconds=15, verify=("http_status",),
            requires_device=True, args_required=("x", "y"),
        ),
        ToolManifest(
            "mac.set_setting", timeout_seconds=20, verify=("setting_applied",),
            requires_device=True, args_required=("key", "value"),
        ),
        ToolManifest(
            "phone.open_url", timeout_seconds=20, verify=("url_opened",),
            requires_device=True, args_required=("url",),
        ),
        ToolManifest(
            "phone.open_settings", timeout_seconds=20, verify=("url_opened",),
            requires_device=True, args_required=("panel",),
        ),
        ToolManifest(
            "phone.call", timeout_seconds=20, verify=("url_opened",),
            requires_device=True, args_required=("number",),
        ),
        ToolManifest(
            "phone.ring", timeout_seconds=15, verify=("http_status",), requires_device=True
        ),
        ToolManifest(
            "phone.system_info", timeout_seconds=15, verify=("http_status",), requires_device=True
        ),
        ToolManifest(
            "phone.torch", timeout_seconds=10, verify=("http_status",), requires_device=True
        ),
        ToolManifest(
            "phone.locate", timeout_seconds=30, verify=("http_status",), requires_device=True
        ),
        ToolManifest(
            "phone.sms_draft", timeout_seconds=20, verify=("url_opened",),
            requires_device=True, args_required=("number", "text"),
        ),
        ToolManifest(
            "phone.open_deeplink", timeout_seconds=20, verify=("url_opened",),
            requires_device=True, args_required=("url",),
        ),
        ToolManifest(
            "phone.clipboard_read", timeout_seconds=10, verify=("http_status",),
            requires_device=True,
        ),
        ToolManifest(
            "phone.clipboard_write", timeout_seconds=10, verify=("http_status",),
            requires_device=True, args_required=("text",),
        ),
        ToolManifest(
            "phone.open_app", timeout_seconds=20, verify=("process_running",),
            requires_device=True, args_required=("bundle_id",),
        ),
        ToolManifest(
            "phone.whatsapp_draft", timeout_seconds=20, verify=("url_opened",),
            requires_device=True, args_required=("phone", "text"),
        ),
        ToolManifest(
            "phone.whatsapp_send", timeout_seconds=30, verify=("key_pressed",),
            requires_device=True, args_required=("phone", "text"),
        ),
        ToolManifest(
            "phone.notify", timeout_seconds=10, verify=("http_status",),
            requires_device=True, args_required=("title", "body"),
        ),
        ToolManifest("focus.start", timeout_seconds=10, verify=("process_running",)),
        ToolManifest("activity.query", timeout_seconds=10, verify=("http_status",)),
        ToolManifest("weather.now", timeout_seconds=20, verify=("http_status",)),
        ToolManifest("news.headlines", timeout_seconds=30, verify=("http_status",)),
        ToolManifest(
            "mac.system_info", timeout_seconds=15, verify=("http_status",), requires_device=True
        ),
        ToolManifest(
            "mac.say", timeout_seconds=20, verify=("http_status",),
            requires_device=True, args_required=("text",),
        ),
        ToolManifest(
            "mac.ring", timeout_seconds=15, verify=("http_status",), requires_device=True
        ),
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
    "url_opened": "url",
    "foreground_window_bundle_id": "bundle_id",
    "dom_selector_present": "selector",
    "window_title_matches": "expect_title",
    "file_exists": "path",
    "setting_applied": "value",
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
