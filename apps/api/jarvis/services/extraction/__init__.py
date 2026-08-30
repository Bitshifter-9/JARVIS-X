"""Deadline extraction: schema-constrained, versioned, cached, vote-on-doubt."""

from jarvis.services.extraction.resolver import ResolutionError, resolve
from jarvis.services.extraction.schema import (
    DEADLINE_JSON_SCHEMA,
    ExtractedDeadline,
    ResolvedDeadline,
)
from jarvis.services.extraction.service import ExtractionOutcome, ExtractionService

__all__ = [
    "DEADLINE_JSON_SCHEMA",
    "ExtractedDeadline",
    "ExtractionOutcome",
    "ExtractionService",
    "ResolutionError",
    "ResolvedDeadline",
    "resolve",
]
