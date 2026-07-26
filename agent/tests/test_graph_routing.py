"""The LangGraph spine (M5) — routing, ordering, and the state durability boundary.

Runs the real graph against real CockroachDB with a scripted FakeModelProvider, so routing
is driven by what `plan()` returns (M4-1: routing IS tool selection) rather than by mocks of
the graph itself. The checkpointer is the real `CockroachDBSaver`, guard included.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import psycopg
import pytest
from langgraph.graph import END, START, StateGraph

from agent.checkpointer import CockroachDBSaver
from agent.graph import (
    STATE_CHANNELS,
    GraphState,
    TurnCarrier,
    build_graph,
    carrier_of,
    run_turn,
)
from agent.tools import AGGREGATE_MEMORIES, COUNT_EVENTS, LOG_MEMORY, RECALL_MEMORIES
from engine.assembly import ContextBlock, RetrievalOutcome
from engine.db import Database
from engine.ingestion import IngestionService, Receipt
from engine.model import ExtractedEvent, ToolCall
from engine.tests.conftest import FakeModelProvider
from engine.trace import EvidenceTrace

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://root@127.0.0.1:26257/defaultdb?sslmode=disable"
)
_DB_REQUIRED = bool(os.environ.get("CI") or os.environ.get("REQUIRE_DB"))
UTC = timezone.utc
IST = "Asia/Kolkata"
NOW = datetime(2026, 7, 24, 6, 40, tzinfo=UTC)
RANGE = {
    "start": (NOW - timedelta(days=30)).isoformat(),
    "end": (NOW + timedelta(days=1)).isoformat(),
}

# The complete set of channels LangGraph may checkpoint for this app (M5-1 L3a). Adding a
# channel must be a conscious act: this test goes red first, and the checkpointer guard (L2)
# is what stops a heavy object regardless.
ALLOWED_CHANNELS = {
    "messages",
    "user_id",
    "question",
    "now",
    "tz",
    "tool_calls",
    "answer",
    "citations",
}
BANNED_TYPES = (ContextBlock, EvidenceTrace, RetrievalOutcome, Receipt)


def _call(tool: str, **arguments) -> ToolCall:
    return ToolCall(tool=tool, arguments=arguments)


def _run_event() -> ExtractedEvent:
    return ExtractedEvent(
        type="workout",
        event_time=NOW - timedelta(hours=1),
        tz=IST,
        confidence=0.9,
        summary="5k run in 28 min",
        payload={"activity": "run", "distance_km": 5.0, "duration_min": 28},
    )


@pytest.fixture(scope="module")
def saver():
    try:
        psycopg.connect(DATABASE_URL, connect_timeout=5).close()
    except psycopg.OperationalError as exc:
        if _DB_REQUIRED:
            raise AssertionError(
                f"CI/REQUIRE_DB set but CockroachDB unreachable at {DATABASE_URL}: {exc}"
            ) from exc
        pytest.skip(f"no CockroachDB reachable at {DATABASE_URL} ({exc}); set REQUIRE_DB=1 to fail")
    database = Database(DATABASE_URL)
    database.setup_schema()
    with CockroachDBSaver.from_conn_string(DATABASE_URL) as built:
        built.setup()
        yield built


@pytest.fixture()
def make_graph(saver):
    """Build a graph around a scripted provider; returns (graph, provider)."""
    database = Database(DATABASE_URL)

    def _build(provider: FakeModelProvider):
        ingestion = IngestionService(database, provider, default_tz=IST)
        graph = build_graph(
            db=database,
            model=provider,
            ingestion=ingestion,
            checkpointer=saver,
            default_tz=IST,
        )
        return graph, provider

    return _build


def _turn(graph, user_id, question: str, thread: str | None = None):
    return run_turn(
        graph,
        user_id=user_id,
        question=question,
        thread_id=thread or f"m5-{uuid.uuid4().hex[:10]}",
        tz=IST,
        now=NOW,
    )


# ── M5-1 L3a: the channel allowlist tripwire ──────────────────────────────────────────
def test_graph_state_declares_only_allowlisted_channels() -> None:
    # If this fails you are adding a channel. That is allowed — but it must be deliberate:
    # heavy, turn-local objects belong on TurnCarrier, never in checkpointed state (M5-1).
    assert set(GraphState.__annotations__) == ALLOWED_CHANNELS
    assert STATE_CHANNELS == ALLOWED_CHANNELS


# ── M5-1 L1: our node wrapper supplies the signal LangGraph does not ──────────────────
def test_langgraph_silently_drops_undeclared_channels() -> None:
    """Pinning test for the upstream behavior our L1 wrapper exists to compensate for.

    Verified on langgraph 1.2.4: an update to an undeclared channel is dropped with no
    exception and no warning. If this ever starts raising — or worse, starts persisting the
    value — this test tells us immediately."""

    class _S(GraphState):
        pass

    builder = StateGraph(_S)
    builder.add_node("n", lambda state, config: {"answer": "ok", "undeclared": object()})
    builder.add_edge(START, "n")
    builder.add_edge("n", END)
    out = builder.compile().invoke({"question": "q"})

    assert out["answer"] == "ok"
    assert "undeclared" not in out  # silently dropped: no raise, no persistence


def test_node_returning_a_heavy_channel_fails_loudly(make_graph) -> None:
    from agent.graph import _checked

    def rogue(state, config):
        return {"context": ContextBlock("q", (), (), (), 0)}

    with pytest.raises(RuntimeError) as excinfo:
        _checked("rogue", rogue)({}, {})
    message = str(excinfo.value)
    assert "M5-1" in message and "TurnCarrier" in message and "context" in message


# ── routing: derived purely from which tools the planner selected (M4-1) ──────────────
def test_query_only_turn_retrieves_and_narrates(make_graph, db, user_id) -> None:
    provider = FakeModelProvider(
        plan_calls=[_call(COUNT_EVENTS, type="workout", **RANGE)],
    )
    graph, _ = make_graph(provider)

    result = _turn(graph, user_id, "how many workouts this month?")

    assert result.receipts == []  # nothing ingested
    assert result.context is not None and result.trace is not None
    assert [s.family for s in result.trace.retrieval_steps] == ["lookup"]
    assert result.answer


def test_ingest_only_turn_writes_a_memory_and_skips_retrieval(make_graph, user_id) -> None:
    provider = FakeModelProvider(
        events=[_run_event()],
        plan_calls=[_call(LOG_MEMORY, text="ran 5k in 28 min")],
    )
    graph, _ = make_graph(provider)

    result = _turn(graph, user_id, "just ran 5k in 28 min")

    assert len(result.receipts) == 1
    assert result.receipts[0].parse_status == "ok"
    assert result.trace is not None  # ADR-12: a turn that narrates always has a trace...
    assert result.trace.retrieval_steps == ()  # ...honestly empty, nothing was retrieved
    assert result.context.is_empty


def test_conversational_turn_on_empty_plan_touches_nothing(make_graph, user_id) -> None:
    provider = FakeModelProvider(plan_calls=[], narration="You're welcome!")
    graph, _ = make_graph(provider)

    result = _turn(graph, user_id, "thanks!")

    assert result.receipts == []
    assert result.trace is not None and result.trace.retrieval_steps == ()
    assert result.citations == []
    assert result.answer == "You're welcome!"
    assert provider.extract_calls == 0  # M4-2: no memory operation at all


def test_both_turn_ingests_before_retrieving(make_graph, user_id) -> None:
    """The load-bearing ordering: a memory logged this turn must already be committed when
    the same turn's aggregate scans for it ('logged my run — am I improving?')."""
    provider = FakeModelProvider(
        events=[_run_event()],
        plan_calls=[
            _call(LOG_MEMORY, text="ran 5k in 28 min"),
            _call(AGGREGATE_MEMORIES, metric="workout_distance_km", agg="sum", **RANGE),
        ],
    )
    graph, _ = make_graph(provider)

    result = _turn(graph, user_id, "ran 5k — how far this month?")

    assert len(result.receipts) == 1
    logged_id = result.receipts[0].created[0].id
    bucket = result.context.aggregates[0].buckets[0]
    assert bucket.value == pytest.approx(5.0)
    assert logged_id in bucket.evidence_ids  # the run counted in its own turn
    assert logged_id in result.context.citable_ids()


# ── honest degradation ────────────────────────────────────────────────────────────────
def test_invalid_tool_call_is_recorded_and_the_turn_still_answers(make_graph, user_id) -> None:
    provider = FakeModelProvider(
        plan_calls=[
            _call(AGGREGATE_MEMORIES, metric="cholesterol", **RANGE),  # unknown metric
            _call(COUNT_EVENTS, type="workout", **RANGE),  # valid
        ],
    )
    graph, _ = make_graph(provider)

    result = _turn(graph, user_id, "cholesterol and workouts?")

    assert len(result.errors) == 1 and "cholesterol" in result.errors[0]
    assert len(result.trace.retrieval_steps) == 1  # the valid call still ran
    assert result.answer


def test_mangled_log_memory_falls_back_to_the_users_own_words(make_graph, user_id) -> None:
    # Never-lose-input posture: a bad text slot must not drop what the user said.
    provider = FakeModelProvider(events=[_run_event()], plan_calls=[_call(LOG_MEMORY)])
    graph, _ = make_graph(provider)

    result = _turn(graph, user_id, "ran 5k in 28 min")

    assert len(result.receipts) == 1
    assert result.receipts[0].created  # the turn was logged, not lost


def test_citations_are_limited_to_ids_the_engine_actually_provided(make_graph, user_id) -> None:
    stranger = uuid.uuid4()
    provider = FakeModelProvider(
        plan_calls=[_call(COUNT_EVENTS, type="workout", **RANGE)],
        narration=f"I made this up [{stranger}].",
    )
    graph, _ = make_graph(provider)

    result = _turn(graph, user_id, "how many workouts?")

    assert str(stranger) not in result.citations  # A3 surface: citable_ids only


# ── conversation continuity + the durability boundary, on the real checkpointer ───────
def test_thread_checkpoint_accumulates_conversation(make_graph, user_id, saver) -> None:
    provider = FakeModelProvider(plan_calls=[], narration="ok")
    graph, _ = make_graph(provider)
    thread = f"m5-thread-{uuid.uuid4().hex[:8]}"

    _turn(graph, user_id, "first question", thread=thread)
    _turn(graph, user_id, "second question", thread=thread)

    loaded = saver.get_tuple({"configurable": {"thread_id": thread}})
    contents = [m.content for m in loaded.checkpoint["channel_values"]["messages"]]
    assert "first question" in contents and "second question" in contents


def test_persisted_checkpoint_contains_no_heavy_objects(make_graph, user_id, saver) -> None:
    """The integration proof of M5-1: after a rich turn (ingest + retrieval + trace), the
    persisted checkpoint holds conversation state only."""
    provider = FakeModelProvider(
        events=[_run_event()],
        plan_calls=[
            _call(LOG_MEMORY, text="ran 5k"),
            _call(AGGREGATE_MEMORIES, metric="workout_distance_km", **RANGE),
            _call(RECALL_MEMORIES, query="running"),
        ],
    )
    graph, _ = make_graph(provider)
    thread = f"m5-durable-{uuid.uuid4().hex[:8]}"

    result = _turn(graph, user_id, "ran 5k — how am I doing?", thread=thread)
    assert result.trace is not None  # the heavy objects DID exist for this turn...

    values = saver.get_tuple({"configurable": {"thread_id": thread}}).checkpoint["channel_values"]
    assert set(values) <= ALLOWED_CHANNELS  # ...and none of them reached the checkpoint
    for name, value in values.items():
        assert not isinstance(value, BANNED_TYPES), f"channel {name} carries a heavy object"
        if isinstance(value, (list, tuple)):
            for item in value:
                assert not isinstance(item, BANNED_TYPES)


# ── the carrier contract ──────────────────────────────────────────────────────────────
def test_missing_carrier_fails_loudly(make_graph, user_id) -> None:
    graph, _ = make_graph(FakeModelProvider(plan_calls=[]))
    with pytest.raises(RuntimeError, match="TurnCarrier"):
        graph.invoke(
            {"user_id": str(user_id), "question": "q", "now": NOW.isoformat(), "tz": IST},
            {"configurable": {"thread_id": f"m5-nocarrier-{uuid.uuid4().hex[:8]}"}},
        )


def test_carrier_of_rejects_a_wrong_shaped_config() -> None:
    with pytest.raises(RuntimeError, match="M5-1"):
        carrier_of({"configurable": {"turn_carrier": {"not": "a carrier"}}})
    assert isinstance(carrier_of({"configurable": {"turn_carrier": TurnCarrier()}}), TurnCarrier)
