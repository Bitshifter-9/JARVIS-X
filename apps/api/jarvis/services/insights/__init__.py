"""Mail intelligence (FEATURES-50: auto-label mail, spending tracker, travel card,
"what changed while away").

Cheap, on-device heuristics — no model call — turn each email into a project label and,
when it is a receipt/bill or a travel confirmation, the numbers behind it. Idempotent:
one ``MailInsight`` per source object, so a re-scan skips what it has already read.
"""

from jarvis.services.insights.service import (
    anomaly_nudges,
    away_digest,
    derive_insights,
    grouped_activity,
    label_for,
    spending_from,
    travel_from,
)

__all__ = [
    "anomaly_nudges",
    "away_digest",
    "grouped_activity",
    "derive_insights",
    "label_for",
    "spending_from",
    "travel_from",
]
