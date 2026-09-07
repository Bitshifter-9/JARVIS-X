"""Slack connector (blueprint §17, PLAN.md phase 5.1)."""

from jarvis.connectors.slack.client import SlackClient, verify_slack_signature
from jarvis.connectors.slack.service import SlackService, normalize_message

__all__ = ["SlackClient", "SlackService", "normalize_message", "verify_slack_signature"]
