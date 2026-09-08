"""Groq rejects a structured request whose schema omits ``additionalProperties`` on any
object — that failure tripped the breaker and took the whole cascade down, so no deadline
was ever extracted. The router normalises every schema on the way out."""

from __future__ import annotations

from jarvis.llm.router import _strict_objects
from jarvis.services.extraction.schema import DEADLINE_JSON_SCHEMA


def test_every_object_gets_additional_properties_false():
    schema = {
        "type": "object",
        "properties": {
            "outer": {"type": "string"},
            "nested": {
                "type": "object",
                "properties": {"deep": {"type": "object", "properties": {}}},
            },
            "listed": {"type": "array", "items": {"type": "object", "properties": {}}},
        },
    }
    out = _strict_objects(schema)
    assert out["additionalProperties"] is False
    assert out["properties"]["nested"]["additionalProperties"] is False
    assert out["properties"]["nested"]["properties"]["deep"]["additionalProperties"] is False
    assert out["properties"]["listed"]["items"]["additionalProperties"] is False
    # Non-objects are untouched.
    assert "additionalProperties" not in out["properties"]["outer"]


def test_an_explicit_setting_is_respected_and_the_constant_is_not_mutated():
    assert _strict_objects({"type": "object", "additionalProperties": True})[
        "additionalProperties"
    ] is True

    before = "additionalProperties" in DEADLINE_JSON_SCHEMA
    out = _strict_objects(DEADLINE_JSON_SCHEMA)
    assert out["additionalProperties"] is False
    assert ("additionalProperties" in DEADLINE_JSON_SCHEMA) == before  # copy, not in place


def test_deadline_schema_is_strict_complete():
    """Strict structured output also demands every property in ``required`` — Groq's second
    rejection after additionalProperties. Optional fields are nullable, so this is free."""
    props = set(DEADLINE_JSON_SCHEMA["properties"])
    assert set(DEADLINE_JSON_SCHEMA["required"]) == props
    # Anything not conceptually required must accept null.
    for key in ("title", "due_at_local", "timezone", "estimate_minutes", "owner",
                "evidence_span", "ambiguity"):
        assert "null" in DEADLINE_JSON_SCHEMA["properties"][key]["type"], key
