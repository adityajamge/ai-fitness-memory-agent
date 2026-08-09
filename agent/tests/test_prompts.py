"""The shared, provider-agnostic model contract (`agent/providers/_prompts.py`).

These pin the extraction tool schema every provider ships, and in particular the
registry-derived payload field guide — added after live validation showed a model inventing
key names (`{"food": "curd"}` instead of `{"name": "curd"}`) because the schema advertised
`payload` as a bare object with no shape. Validation then rejected the whole turn into a
note, so a working extraction became an "incomplete parse".
"""

from __future__ import annotations

import pytest

from agent.providers._prompts import (
    EXTRACT_TOOL,
    _describe_field,
    _is_engine_owned,
    extract_tool_schema,
    payload_field_guide,
)
from engine.types import MEMORY_TYPE_REGISTRY, MealPayload


def test_guide_covers_every_registered_memory_type() -> None:
    guide = payload_field_guide()
    for memory_type in MEMORY_TYPE_REGISTRY:
        assert f"{memory_type}:" in guide, memory_type


def test_guide_lists_every_typed_hot_field() -> None:
    """Drift guard: the guide is generated from the registry, so a new hot field in
    engine/types.py must appear in the prompt without anyone editing prose.

    Engine-owned fields are the one exception, and they are excluded *by the marker on the
    field* rather than by a name listed here — so this stays a drift guard rather than a
    hand-maintained allowlist that would rot the moment someone adds another."""
    guide = payload_field_guide()
    for model in MEMORY_TYPE_REGISTRY.values():
        for field_name, field in model.model_fields.items():
            if _is_engine_owned(field):
                continue
            assert field_name in guide, f"{model.__name__}.{field_name} missing from the guide"


def test_guide_omits_engine_owned_fields() -> None:
    """`payload.nutrition` is written by the dedicated nutrition call and validated by
    engine/nutrition.py — the extraction model must not be told it exists.

    This is the fix for the bug that motivated the whole nutrition stage: with `nutrition`
    advertised here, extraction filled it *sometimes*, under a prompt that simultaneously said
    "NEVER invent facts". The same meal logged three times produced macros, no macros, and
    different macros. Two model calls owning one key is the defect; this asserts there is now
    exactly one owner."""
    guide = payload_field_guide()
    assert "nutrition" not in guide
    assert "protein_g" not in guide
    assert MealPayload.model_fields["nutrition"].json_schema_extra == {"engine_owned": True}


def test_guide_reaches_into_nested_payload_models() -> None:
    """The regression that motivated this: `name` lives on MealItem, one level inside
    MealPayload.items — a top-level-only guide would not have surfaced it."""
    guide = payload_field_guide()
    assert "items: [{name" in guide  # list-of-model rendering
    assert "retraction_condition: {metric" in guide  # single nested model rendering
    for field_name, field in MealPayload.model_fields.items():
        if _is_engine_owned(field):
            continue
        assert field_name in guide


def test_guide_marks_numeric_fields_with_a_type_hint() -> None:
    """The regression that motivated this: `qty_g`/`qty` carried no type hint, so a live
    model returned "250g" (a string) into a float field — same guessing failure as the
    key-name bug, one level deeper. Every float/int hot field must say `:number`."""
    guide = payload_field_guide()
    assert "qty_g:number" in guide
    assert "qty:number" in guide  # nested one level inside MealPayload.items
    assert "pre_value:number" in guide  # nested inside InsightPayload
    assert "distance_km:number" in guide
    assert "body_fat_pct:number" in guide
    # `name` stays bare — string is the schema's implicit default, no hint needed.
    assert "name:" not in guide


def test_boolean_fields_get_a_type_hint() -> None:
    """The `:boolean` half of the same rule, asserted on the renderer directly.

    It used to ride on `Nutrition.estimated`, which is no longer advertised to the extraction
    model (it is engine-owned). Pinning the renderer instead of borrowing whichever payload
    happens to hold a bool keeps the rule covered without tying it to a field that may move."""
    assert _describe_field("flagged", bool) == "flagged:boolean"
    assert _describe_field("flagged", bool | None) == "flagged:boolean"  # the Optional shape
    assert _describe_field("note", str) == "note"


def test_extraction_tool_explains_the_number_convention() -> None:
    schema = extract_tool_schema()
    payload = schema["input_schema"]["properties"]["events"]["items"]["properties"]["payload"]
    description = payload["description"]
    assert ":number" in description
    assert "200g" in description  # the concrete counter-example a model must not produce


def test_extraction_tool_instructs_summary_to_include_quantities() -> None:
    """The regression that motivated this: EvidenceSnapshot is deliberately payload-free
    (ADR-12, engine/trace.py) — summary is the ONLY text a later question's evidence ever
    shows, so a vague model-written summary ("had lunch with eggs") makes a follow-up
    question about quantity permanently unanswerable, with no engine-layer fix possible."""
    schema = extract_tool_schema()
    summary = schema["input_schema"]["properties"]["events"]["items"]["properties"]["summary"]
    description = summary.get("description", "")
    assert "quantities" in description
    assert "3 eggs" in description  # the concrete example a model should imitate


def test_extraction_tool_embeds_the_guide_and_forbids_renaming() -> None:
    schema = extract_tool_schema()
    assert schema["name"] == EXTRACT_TOOL
    payload = schema["input_schema"]["properties"]["events"]["items"]["properties"]["payload"]
    description = payload["description"]
    assert payload_field_guide() in description
    assert "EXACTLY these key names" in description
    # extra="allow" on the registry models means unknown keys survive — say so, or the
    # model drops detail it could legitimately have kept.
    assert "extra keys" in description


@pytest.mark.parametrize("required", ["events", "no_loggable_content"])
def test_empty_result_contract_still_enforced_at_schema_level(required) -> None:
    """The D1 flag must stay required — adding the payload guide must not disturb it."""
    schema = extract_tool_schema()["input_schema"]
    assert required in schema["required"]
