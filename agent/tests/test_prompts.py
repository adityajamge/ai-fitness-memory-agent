"""The shared, provider-agnostic model contract (`agent/providers/_prompts.py`).

These pin the extraction tool schema every provider ships, and in particular the
registry-derived payload field guide — added after live validation showed a model inventing
key names (`{"food": "curd"}` instead of `{"name": "curd"}`) because the schema advertised
`payload` as a bare object with no shape. Validation then rejected the whole turn into a
note, so a working extraction became an "incomplete parse".
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agent.providers._prompts import (
    EXTRACT_TOOL,
    _describe_field,
    _is_engine_owned,
    extract_tool_schema,
    history_messages,
    payload_field_guide,
)
from engine.model import HistoryTurn
from engine.types import ENGINE_ONLY_TYPES, MEMORY_TYPE_REGISTRY, MealPayload

#: Types the extraction model may be offered — the registry minus ENGINE_ONLY_TYPES (ADR-17.1:
#: `profile_change` is written only by the profile router, and its trivially-satisfiable payload
#: shape means it must never be a type the extractor can propose).
_EXTRACTABLE = {k: v for k, v in MEMORY_TYPE_REGISTRY.items() if k not in ENGINE_ONLY_TYPES}


def test_guide_covers_every_extractable_memory_type() -> None:
    guide = payload_field_guide()
    for memory_type in _EXTRACTABLE:
        assert f"{memory_type}:" in guide, memory_type


def test_guide_omits_engine_only_types() -> None:
    """`profile_change` must never reach the extraction prompt (ADR-17.1) — unlike `insight`,
    its payload has no coherence check, so a stray extraction would validate trivially."""
    guide = payload_field_guide()
    for memory_type in ENGINE_ONLY_TYPES:
        assert f"{memory_type}:" not in guide, memory_type


def test_guide_lists_every_typed_hot_field() -> None:
    """Drift guard: the guide is generated from the registry, so a new hot field in
    engine/types.py must appear in the prompt without anyone editing prose.

    Engine-owned fields are the one exception, and they are excluded *by the marker on the
    field* rather than by a name listed here — so this stays a drift guard rather than a
    hand-maintained allowlist that would rot the moment someone adds another. Scoped to
    ``_EXTRACTABLE`` for the same reason as the test above: an ``ENGINE_ONLY_TYPES`` field is
    correctly absent from the guide, not a drift bug."""
    guide = payload_field_guide()
    for model in _EXTRACTABLE.values():
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


# ── short-term memory renders to a LEGAL message array (ADR-14.16) ────────────────────
#
# Both providers reject a malformed conversation, and the malformed shapes are produced by
# ordinary budget/scrub behaviour rather than by anything exotic — so these are regression
# tests for a 400/ValidationException in production, not style checks.
def _h(role: str, content: str, day: int = 16) -> HistoryTurn:
    return HistoryTurn(role=role, content=content, at=datetime(2026, 8, day, tzinfo=timezone.utc))


def test_history_renders_with_absolute_dates_per_message() -> None:
    """An earlier 'today' must be readable as the date it was said, not the current one."""
    assert history_messages([_h("user", "today i ate 3 eggs"), _h("assistant", "Logged.", 17)]) == [
        {"role": "user", "content": "[Aug 16] today i ate 3 eggs"},
        {"role": "assistant", "content": "[Aug 17] Logged."},
    ]


def test_a_window_starting_mid_exchange_drops_the_orphan_answer() -> None:
    """`max_turns=3` over two exchanges starts the window on an assistant message. Sent as-is
    that is a 400 on the Claude API and a ValidationException on Converse."""
    out = history_messages([_h("assistant", "an answer"), _h("user", "q"), _h("assistant", "a")])

    assert [m["role"] for m in out] == ["user", "assistant"]
    assert "an answer" not in out[0]["content"]


def test_a_trailing_question_is_dropped_so_the_current_turn_can_follow() -> None:
    """History must end with an assistant message: the provider appends the current turn as a
    user message, and two user messages in a row break Converse's alternation rule."""
    out = history_messages([_h("user", "q1"), _h("assistant", "a1"), _h("user", "dangling")])

    assert [m["role"] for m in out] == ["user", "assistant"]
    assert "dangling" not in out[-1]["content"]


def test_consecutive_same_role_messages_are_merged_not_dropped() -> None:
    """An assistant answer that was only citation markers scrubs to empty and disappears,
    leaving two user messages adjacent. Merging keeps both, alternation stays legal."""
    out = history_messages(
        [_h("user", "first"), _h("user", "second"), _h("assistant", "reply")]
    )

    assert [m["role"] for m in out] == ["user", "assistant"]
    assert "first" in out[0]["content"] and "second" in out[0]["content"]


def test_rendered_history_always_alternates_and_brackets_correctly() -> None:
    """The invariant every provider relies on, over every awkward shape at once."""
    shapes = [
        [],
        [_h("assistant", "only an answer")],
        [_h("user", "only a question")],
        [_h("assistant", "a"), _h("user", "q")],
        [_h("user", "q"), _h("assistant", "a"), _h("user", "q2"), _h("assistant", "a2")],
    ]
    for shape in shapes:
        out = history_messages(shape)
        if not out:
            continue
        assert out[0]["role"] == "user", shape
        assert out[-1]["role"] == "assistant", shape
        roles = [m["role"] for m in out]
        assert all(a != b for a, b in zip(roles, roles[1:], strict=False)), shape
