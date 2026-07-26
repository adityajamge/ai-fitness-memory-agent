"""Tool-layer tests (M5 — `agent/tools.py`).

Two halves, matching the module's two jobs:
  * **validation** (DB-free): the planner's raw arguments become typed specs, and every
    malformed call dies here — above the database, as 05's query-planning boundary requires.
  * **execution** (real single-node/cloud CockroachDB): a prepared call runs the right engine
    builder and comes back as a RetrievalOutcome that assembly accepts.

The closing test walks a scripted plan() through prepare → execute → assemble, which is the
exact sequence the M5 graph will perform.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from agent.tools import (
    AGGREGATE_MEMORIES,
    COUNT_EVENTS,
    GET_TIMELINE,
    LOG_MEMORY,
    LOOKUP_EVENTS,
    RECALL_MEMORIES,
    RETRIEVAL_TOOLS,
    TOOL_NAMES,
    PreparedCall,
    ToolCallError,
    build_tool_specs,
    execute,
    is_log_memory,
    log_memory_text,
    prepare_call,
)
from engine.assembly import RetrievalOutcome, assemble
from engine.memory import Memory
from engine.model import EmbeddingError, ToolCall
from engine.repository import insert_memories
from engine.retrieval import (
    METRICS,
    AggregateResult,
    AggregateSpec,
    CountResult,
    CountSpec,
    LookupResult,
    LookupSpec,
    RecallResult,
    RecallSpec,
    TimelineResult,
    TimelineSpec,
)
from engine.tests.conftest import FakeModelProvider
from engine.types import MEMORY_TYPE_REGISTRY

UTC = timezone.utc
IST = "Asia/Kolkata"
START = "2026-07-01T00:00:00+00:00"
END = "2026-08-01T00:00:00+00:00"
T0 = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)


def _call(tool: str, **arguments) -> ToolCall:
    return ToolCall(tool=tool, arguments=arguments)


def _prepare(call: ToolCall, *, model=None, tz: str = IST):
    return prepare_call(call, model=model or FakeModelProvider(), tz=tz)


# ── 1. vocabulary ─────────────────────────────────────────────────────────────────────
def test_tool_specs_cover_exactly_the_known_tools() -> None:
    specs = build_tool_specs()
    assert {s.name for s in specs} == TOOL_NAMES
    # log_memory is offered so that selecting it IS the ingest classification (M4-1).
    assert LOG_MEMORY in {s.name for s in specs}


def test_schemas_enumerate_the_engines_closed_vocabularies() -> None:
    by_name = {s.name: s.input_schema for s in build_tool_specs()}
    # A planner cannot name a metric or memory type the engine does not have — the schema
    # itself is the constraint (same posture as the extraction tool's type enum).
    assert by_name[AGGREGATE_MEMORIES]["properties"]["metric"]["enum"] == sorted(METRICS)
    assert by_name[COUNT_EVENTS]["properties"]["type"]["enum"] == sorted(MEMORY_TYPE_REGISTRY)
    assert by_name[LOOKUP_EVENTS]["properties"]["type"]["enum"] == sorted(MEMORY_TYPE_REGISTRY)


def test_timezone_is_never_a_planner_slot() -> None:
    # tz is a property of the user, not of the question: the engine injects it, so the model
    # can never invent one.
    for schema in (s.input_schema for s in build_tool_specs()):
        assert "tz" not in schema["properties"]
        assert "timezone" not in schema["properties"]


def test_required_slots_are_declared() -> None:
    by_name = {s.name: s.input_schema for s in build_tool_specs()}
    assert set(by_name[AGGREGATE_MEMORIES]["required"]) == {"metric", "start", "end"}
    assert by_name[RECALL_MEMORIES]["required"] == ["query"]
    assert by_name[LOG_MEMORY]["required"] == ["text"]


# ── 2. validation: raw arguments → typed specs ────────────────────────────────────────
def test_aggregate_call_becomes_a_typed_spec_with_injected_tz() -> None:
    prepared = _prepare(
        _call(
            AGGREGATE_MEMORIES, metric="protein_g", agg="avg", group_by="week", start=START, end=END
        )
    )
    assert isinstance(prepared.spec, AggregateSpec)
    assert prepared.spec.metric == "protein_g"
    assert prepared.spec.agg == "avg"
    assert prepared.spec.group_by == "week"
    assert prepared.spec.tz == IST  # injected, not planner-supplied
    assert prepared.query_vec is None


def test_aggregate_defaults_are_applied_when_slots_omitted() -> None:
    prepared = _prepare(_call(AGGREGATE_MEMORIES, metric="protein_g", start=START, end=END))
    assert prepared.spec.agg == "sum"
    assert prepared.spec.group_by == "none"


def test_recall_call_embeds_the_query_during_preparation() -> None:
    model = FakeModelProvider()
    prepared = _prepare(_call(RECALL_MEMORIES, query="knee pain", top_k=5), model=model)

    assert isinstance(prepared.spec, RecallSpec)
    assert prepared.spec.top_k == 5
    # The engine embeds (D-4), and it happens in prepare — outside any transaction.
    assert model.embed_calls == 1
    assert prepared.query_vec is not None and len(prepared.query_vec) == 512


def test_recall_embedding_failure_surfaces_before_execution() -> None:
    with pytest.raises(EmbeddingError):
        _prepare(_call(RECALL_MEMORIES, query="q"), model=FakeModelProvider(embed_error=True))


def test_timeline_and_lookup_and_count_specs() -> None:
    tl = _prepare(_call(GET_TIMELINE, start=START, end=END, types=["meal", "sleep"], limit=50))
    assert isinstance(tl.spec, TimelineSpec)
    assert tl.spec.types == ("meal", "sleep")
    assert tl.spec.limit == 50

    lk = _prepare(_call(LOOKUP_EVENTS, type="meal", item="chicken", direction="first", n=3))
    assert isinstance(lk.spec, LookupSpec)
    assert (lk.spec.item, lk.spec.direction, lk.spec.n) == ("chicken", "first", 3)

    ct = _prepare(_call(COUNT_EVENTS, type="workout", start=START, end=END))
    assert isinstance(ct.spec, CountSpec)
    assert ct.spec.type == "workout"


def test_naive_timestamps_are_localized_to_the_user_timezone() -> None:
    prepared = _prepare(
        _call(
            AGGREGATE_MEMORIES,
            metric="protein_g",
            start="2026-07-01T00:00:00",
            end="2026-08-01T00:00:00",
        )
    )
    assert prepared.spec.start.tzinfo is not None
    assert prepared.spec.start.utcoffset() == timedelta(hours=5, minutes=30)  # IST


def test_z_suffix_timestamps_parse() -> None:
    prepared = _prepare(
        _call(
            AGGREGATE_MEMORIES,
            metric="protein_g",
            start="2026-07-01T00:00:00Z",
            end="2026-08-01T00:00:00Z",
        )
    )
    assert prepared.spec.start == datetime(2026, 7, 1, tzinfo=UTC)


# ── validation failures: every planner mistake dies above the database ────────────────
@pytest.mark.parametrize(
    "call",
    [
        _call("analyze_series", metric="body_fat_pct"),  # Phase 5 tool, not yet real
        _call("drop_table", sql="DROP TABLE memories"),  # not in the vocabulary
        _call(AGGREGATE_MEMORIES, start=START, end=END),  # missing metric
        _call(AGGREGATE_MEMORIES, metric="protein_g", end=END),  # missing start
        _call(AGGREGATE_MEMORIES, metric="cholesterol", start=START, end=END),  # unknown metric
        _call(AGGREGATE_MEMORIES, metric="protein_g", agg="median", start=START, end=END),
        _call(AGGREGATE_MEMORIES, metric="protein_g", group_by="month", start=START, end=END),
        _call(AGGREGATE_MEMORIES, metric="protein_g", start="last tuesday", end=END),
        _call(AGGREGATE_MEMORIES, metric="protein_g", start=END, end=START),  # inverted range
        _call(RECALL_MEMORIES),  # missing query
        _call(RECALL_MEMORIES, query="q", top_k=0),  # out of range
        _call(RECALL_MEMORIES, query="q", top_k="five"),  # wrong type
        _call(RECALL_MEMORIES, query="q", type="diary"),  # unknown memory type
        _call(GET_TIMELINE, start=START, end=END, types="meal"),  # not a list
        _call(GET_TIMELINE, start=START, end=END, limit=99999),  # over cap
        _call(LOOKUP_EVENTS, type="meal", direction="latest"),  # bad enum
        _call(LOOKUP_EVENTS, type="meal", n=999),  # over cap
        _call(COUNT_EVENTS, type="workout", start=START),  # missing end
    ],
)
def test_invalid_calls_raise_tool_call_error(call) -> None:
    with pytest.raises(ToolCallError):
        _prepare(call)


def test_missing_date_range_is_not_defaulted_to_a_guessed_window() -> None:
    # Inventing a range would answer a question the user did not ask — strict by design.
    with pytest.raises(ToolCallError, match="missing required 'start'"):
        _prepare(_call(AGGREGATE_MEMORIES, metric="protein_g", end=END))


def test_log_memory_is_dispatched_by_the_graph_not_prepare_call() -> None:
    call = _call(LOG_MEMORY, text="250g curd")
    assert is_log_memory(call)
    assert log_memory_text(call) == "250g curd"
    with pytest.raises(ToolCallError):
        _prepare(call)


def test_log_memory_requires_non_empty_text() -> None:
    with pytest.raises(ToolCallError):
        log_memory_text(_call(LOG_MEMORY, text="  "))
    with pytest.raises(ToolCallError):
        log_memory_text(_call(LOG_MEMORY))


def test_non_dict_arguments_fail_as_validation_errors_not_crashes() -> None:
    # A malformed provider payload must surface as ToolCallError, never AttributeError.
    with pytest.raises(ToolCallError):
        _prepare(ToolCall(tool=AGGREGATE_MEMORIES, arguments=None))  # type: ignore[arg-type]
    with pytest.raises(ToolCallError):
        log_memory_text(ToolCall(tool=LOG_MEMORY, arguments=None))  # type: ignore[arg-type]


def test_empty_types_filter_is_rejected_not_read_as_no_filter() -> None:
    # "filter to nothing" must not be silently reinterpreted as "no filter at all".
    with pytest.raises(ToolCallError):
        _prepare(_call(GET_TIMELINE, start=START, end=END, types=[]))


def test_execute_rejects_a_hand_built_recall_call_without_a_vector() -> None:
    prepared = PreparedCall(tool=RECALL_MEMORIES, spec=RecallSpec(query="q"), query_vec=None)
    with pytest.raises(ToolCallError):
        execute(None, uuid4(), prepared)  # type: ignore[arg-type]  — fails before any SQL


# ── 3. execution against a real database ──────────────────────────────────────────────
def _meal(user_id: UUID, event_time: datetime, protein: float, summary: str = "lunch") -> Memory:
    return Memory(
        user_id=user_id,
        event_time=event_time,
        tz=IST,
        type="meal",
        source="chat",
        provenance="live",
        confidence=0.9,
        payload={
            "meal_type": "lunch",
            "items": [{"name": "chicken"}],
            "nutrition": {"protein_g": protein},
        },
        summary=summary,
    )


@pytest.fixture()
def seeded(db, user_id):
    model = FakeModelProvider()
    meals = [
        _meal(user_id, T0, 30, "lunch: 100g chicken"),
        _meal(user_id, T0 + timedelta(days=1), 40, "dinner: 200g chicken"),
    ]
    for m in meals:
        m.embedding = model.embed([m.summary])[0]
    with db.transaction() as cur:
        ids = insert_memories(cur, meals)
    return ids


@pytest.mark.parametrize(
    ("call", "result_type", "family"),
    [
        (
            _call(AGGREGATE_MEMORIES, metric="protein_g", start=START, end=END),
            AggregateResult,
            "aggregate",
        ),
        (_call(RECALL_MEMORIES, query="chicken dinner"), RecallResult, "recall"),
        (_call(GET_TIMELINE, start=START, end=END), TimelineResult, "timeline"),
        (_call(LOOKUP_EVENTS, type="meal", item="chicken"), LookupResult, "lookup"),
        (_call(COUNT_EVENTS, type="meal", start=START, end=END), CountResult, "lookup"),
    ],
)
def test_each_tool_executes_into_a_retrieval_outcome(
    db, user_id, seeded, call, result_type, family
) -> None:
    prepared = _prepare(call)
    with db.transaction() as cur:
        outcome = execute(cur, user_id, prepared)

    assert isinstance(outcome, RetrievalOutcome)
    assert isinstance(outcome.result, result_type)
    assert outcome.step.family == family
    assert outcome.step.row_count >= 1


def test_execution_is_user_scoped(db, user_id, seeded) -> None:
    prepared = _prepare(_call(AGGREGATE_MEMORIES, metric="protein_g", start=START, end=END))
    with db.transaction() as cur:
        mine = execute(cur, user_id, prepared)
        theirs = execute(cur, uuid4(), prepared)

    assert mine.result.buckets[0].value == pytest.approx(70.0)
    assert theirs.result.is_empty  # another user's aggregate sees nothing


def test_every_retrieval_tool_is_executable(db, user_id) -> None:
    # Guards against a tool being added to the vocabulary but not to execute()'s dispatch.
    executable = set()
    for tool, args in [
        (AGGREGATE_MEMORIES, {"metric": "protein_g", "start": START, "end": END}),
        (RECALL_MEMORIES, {"query": "q"}),
        (GET_TIMELINE, {"start": START, "end": END}),
        (LOOKUP_EVENTS, {"type": "meal"}),
        (COUNT_EVENTS, {"type": "meal", "start": START, "end": END}),
    ]:
        prepared = _prepare(_call(tool, **args))
        with db.transaction() as cur:
            execute(cur, user_id, prepared)
        executable.add(tool)
    assert executable == set(RETRIEVAL_TOOLS)


# ── 4. the M5 graph's sequence, end to end (plan → prepare → execute → assemble) ──────
def test_scripted_plan_flows_through_prepare_execute_assemble(db, user_id, seeded) -> None:
    question = "how much protein this month, and when did I last eat chicken?"
    model = FakeModelProvider(
        plan_calls=[
            _call(AGGREGATE_MEMORIES, metric="protein_g", start=START, end=END),
            _call(LOOKUP_EVENTS, type="meal", item="chicken"),
            _call(RECALL_MEMORIES, query="chicken"),  # the fuzzy companion (M2 review)
        ]
    )

    calls = model.plan(question, build_tool_specs(), now=T0, tz=IST)
    prepared = [prepare_call(c, model=model, tz=IST) for c in calls]  # off-transaction
    with db.transaction() as cur:
        outcomes = [execute(cur, user_id, p) for p in prepared]

    context, trace = assemble(question, outcomes)

    assert len(trace.retrieval_steps) == 3
    assert {s.family for s in trace.retrieval_steps} == {"aggregate", "lookup", "recall"}
    assert context.aggregates[0].buckets[0].value == pytest.approx(70.0)
    assert set(seeded) <= context.citable_ids()
    # The narrator can now cite everything the engine actually used.
    answer = model.narrate(question, context)
    for cid in context.citable_ids():
        assert f"[{cid}]" in answer
