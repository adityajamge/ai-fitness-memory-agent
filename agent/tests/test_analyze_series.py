"""``analyze_series`` — a write tool the graph dispatches (Phase 5 M5c, §4.9).

The point of this milestone is a boundary, not a feature. Every other retrieval tool is a
question; this one is a *verb*. It may write an insight, and `execute()` runs every retrieval
tool inside one shared transaction — so a write there would need a read, a compare, and
possibly an insert+supersede inside that transaction, dragging several round trips and the
§4.8 budget somewhere they must never go.

So it is dispatched exactly like ``log_memory``: at graph level, to a service, outside the
retrieve transaction. What is tested here is that the boundary actually holds.

* **I-17** — the closed retrieval set stays read-only, structurally: ``analyze_series`` is not
  in it, and ``prepare_call`` refuses it.
* **ADR-14.3, extended to tier 2** — consolidate runs *before* retrieve, so an insight derived
  this turn is visible to the same turn's ``lookup_insights``. Otherwise the engine would
  derive a claim and then answer as though it had not.
* **ADR-14.12** — a failed tool costs that tool, not the turn.
"""

from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from agent import graph as graph_module
from agent.graph import TurnCarrier, build_graph, run_turn
from agent.tools import (
    ANALYZE_SERIES,
    LOOKUP_INSIGHTS,
    RETRIEVAL_TOOLS,
    WRITE_TOOLS,
    ToolCallError,
    prepare_call,
)
from engine.consolidation import ConsolidationService
from engine.ingestion import IngestionService
from engine.model import ExtractedEvent, ToolCall
from engine.tests.conftest import FakeModelProvider

TZ = "Asia/Kolkata"
IST = timezone(timedelta(hours=5, minutes=30))


def _at(day: str) -> datetime:
    return datetime.fromisoformat(f"{day}T12:00:00").replace(tzinfo=IST)


def _meal(day: str, protein: float, composition: str) -> ExtractedEvent:
    return ExtractedEvent(
        type="meal", event_time=_at(day), tz=TZ, confidence=1.0, summary=f"meal {protein:g}g",
        payload={
            "nutrition": {"protein_g": protein}, "items": [],
            "expanded_from": {"composition": composition, "assertion": f"{protein:g} g/day"},
        },
    )


def _phase(start: str, days: int, protein: float, composition: str) -> list[ExtractedEvent]:
    first = datetime.fromisoformat(start).date()
    return [
        _meal((first + timedelta(days=n)).isoformat(), protein, composition)
        for n in range(days)
    ]


def _call(tool: str, **args) -> ToolCall:
    return ToolCall(tool=tool, arguments=args)


@pytest.fixture()
def seeded(db, user_id):
    """A protein series with a shift in it, but **no insight derived yet** — so the turn under
    test is the one that derives it."""
    quiet = IngestionService(db, FakeModelProvider([]), default_tz=TZ)  # no consolidation
    quiet.ingest_events(user_id, _phase("2026-05-01", 8, 30.0, "phase-a"), provenance="live")
    quiet.ingest_events(user_id, _phase("2026-05-09", 8, 60.0, "phase-b"), provenance="live")
    return user_id


def _graph(db, provider, *, consolidation=True):
    ingestion = IngestionService(db, provider, default_tz=TZ)
    return build_graph(
        db=db, model=provider, ingestion=ingestion, checkpointer=None, default_tz=TZ,
        consolidation=(
            ConsolidationService(db, default_tz=TZ, budget_ms=60_000) if consolidation else None
        ),
    )


def _turn(graph, user_id: UUID, question: str = "analyse my protein"):
    return run_turn(
        graph, user_id=user_id, question=question,
        thread_id=f"m5c-{uuid4().hex[:8]}", tz=TZ, now=_at("2026-05-20"),
    )


def _insight_count(db, user_id: UUID) -> int:
    with db.transaction() as cur:
        cur.execute(
            "SELECT count(*) AS n FROM memories WHERE user_id = %s AND type = 'insight'",
            [user_id],
        )
        return int(cur.fetchone()["n"])


# ══ I-17 — the write tool is outside the read-only builder set ═════════════════════════
def test_analyze_series_is_not_in_the_retrieval_set():
    """**I-17**, structurally. The closed builder set stays read-only because the writer is
    not a member of it — not because everyone remembers not to write."""
    assert ANALYZE_SERIES not in RETRIEVAL_TOOLS
    assert ANALYZE_SERIES in WRITE_TOOLS


def test_prepare_call_refuses_the_write_tools():
    """The other half: even if something routed it here, it dies before any SQL."""
    for tool in WRITE_TOOLS:
        with pytest.raises(ToolCallError, match="dispatched by the graph"):
            prepare_call(_call(tool, metric="protein_g"), model=FakeModelProvider([]), tz=TZ)


def test_execute_never_sees_a_write_tool():
    """``execute`` dispatches only the read families — there is no branch that could write."""
    source = inspect.getsource(graph_module)
    assert "execute(cur, user_id, p) for p in prepared" in source
    from agent import tools as tools_module

    execute_src = inspect.getsource(tools_module.execute)
    assert ANALYZE_SERIES not in execute_src


def test_the_tool_is_still_offered_to_the_planner():
    """Routing is tool selection (ADR-14.1): the planner must be able to *choose* it, even
    though the graph — not the tool layer — executes it."""
    from agent.tools import build_tool_specs

    names = {spec.name for spec in build_tool_specs()}
    assert ANALYZE_SERIES in names


def test_the_metric_slot_is_strict():
    """ADR-14.11: an unknown or missing series is rejected, never defaulted to 'analyse
    everything' — a different, and far more expensive, request than the one that was made."""
    from agent.tools import analyze_series_metric

    assert analyze_series_metric(_call(ANALYZE_SERIES, metric="protein_g")) == "protein_g"
    for bad in ({}, {"metric": "horoscope"}, {"metric": "carbs_g"}, {"metric": 7}):
        with pytest.raises(ToolCallError):
            analyze_series_metric(_call(ANALYZE_SERIES, **bad))


# ══ the tool actually derives, through the service ═════════════════════════════════════
def test_analyze_series_derives_an_insight(db, seeded):
    provider = FakeModelProvider(plan_calls=[_call(ANALYZE_SERIES, metric="protein_g")])
    graph = _graph(db, provider)

    result = _turn(graph, seeded)

    assert _insight_count(db, seeded) == 1
    assert len(result.consolidation) == 1
    assert result.consolidation[0].created is not None
    assert not result.errors


def test_it_writes_only_through_the_consolidation_service(db, seeded):
    """The node holds no SQL of its own: every write goes through ConsolidationService, so
    the identity rule lives in exactly one place (I-12)."""
    source = inspect.getsource(graph_module)
    node = source.split("def consolidate_node")[1].split("def retrieve_node")[0]
    for forbidden in ("INSERT", "UPDATE", "insert_memories", "mark_", "db.transaction"):
        assert forbidden not in node
    assert "consolidation.consolidate_series" in node


def test_a_second_identical_turn_writes_nothing(db, seeded):
    """I-12 reaches through the tool too: re-analysing an unchanged series is a no-op."""
    provider = FakeModelProvider(plan_calls=[_call(ANALYZE_SERIES, metric="protein_g")])
    graph = _graph(db, provider)

    _turn(graph, seeded)
    second = _turn(graph, seeded)

    assert _insight_count(db, seeded) == 1
    assert second.consolidation[0].created is None
    assert second.consolidation[0].unchanged is not None


# ══ ADR-14.3 — consolidate before retrieve, so the turn sees its own work ══════════════
def test_an_insight_derived_this_turn_is_retrievable_in_the_same_turn(db, seeded):
    """The ordering that makes the tool worth having. Without it the engine would derive a
    claim and then answer as though it had not — a wrong answer that looks right."""
    provider = FakeModelProvider(
        plan_calls=[
            _call(ANALYZE_SERIES, metric="protein_g"),
            _call(LOOKUP_INSIGHTS, metric="protein_g"),
        ]
    )
    graph = _graph(db, provider)

    result = _turn(graph, seeded, "analyse my protein and tell me what you found")

    derived = result.consolidation[0].created
    assert derived is not None
    # The very insight this turn wrote came back through retrieval, in the same turn.
    assert [i.id for i in result.context.insights] == [derived]
    assert derived in result.context.citable_ids()
    assert [ref.id for ref in result.trace.insights] == [derived]


def test_the_stage_order_is_ingest_then_consolidate_then_retrieve():
    """One table drives every edge, so a stage cannot be reachable from one predecessor and
    unreachable from another."""
    from agent.graph import _next_stage

    everything = {"tool_calls": [{"tool": t} for t in
                                 ("log_memory", ANALYZE_SERIES, "aggregate_memories")]}
    assert _next_stage(everything, after=None) == "ingest"
    assert _next_stage(everything, after="ingest") == "consolidate"
    assert _next_stage(everything, after="consolidate") == "retrieve"


def test_a_turn_that_only_analyses_skips_retrieval():
    from agent.graph import _next_stage

    only = {"tool_calls": [{"tool": ANALYZE_SERIES}]}
    assert _next_stage(only, after=None) == "consolidate"
    assert _next_stage(only, after="consolidate") == "assemble"


def test_an_empty_plan_still_reaches_assemble():
    """M4-2: a conversational turn is answered without inventing a retrieval."""
    from agent.graph import _next_stage

    assert _next_stage({"tool_calls": []}, after=None) == "assemble"


def test_ingest_then_analyze_sees_the_memory_it_just_logged(db, user_id):
    """Both write stages in one turn, in order: the meal is committed before the series is
    analysed, so the analysis includes it."""
    provider = FakeModelProvider(
        events=_phase("2026-05-09", 8, 60.0, "phase-b"),
        plan_calls=[
            _call("log_memory", text="another week at the same level"),
            _call(ANALYZE_SERIES, metric="protein_g"),
        ],
    )
    quiet = IngestionService(db, FakeModelProvider([]), default_tz=TZ)
    quiet.ingest_events(user_id, _phase("2026-05-01", 8, 30.0, "phase-a"), provenance="live")

    graph = _graph(db, provider)
    result = _turn(graph, user_id, "logged another week — analyse it")

    assert result.receipts and result.receipts[0].created
    assert result.consolidation[0].created is not None


# ══ ADR-14.12 — a failed tool costs that tool, not the turn ════════════════════════════
class _ExplodingConsolidator(ConsolidationService):
    def consolidate_series(self, *args, **kwargs):  # noqa: D102
        raise RuntimeError("consolidation blew up")


def test_a_failing_analysis_does_not_sink_the_turn(db, seeded):
    provider = FakeModelProvider(plan_calls=[_call(ANALYZE_SERIES, metric="protein_g")])
    ingestion = IngestionService(db, provider, default_tz=TZ)
    graph = build_graph(
        db=db, model=provider, ingestion=ingestion, checkpointer=None, default_tz=TZ,
        consolidation=_ExplodingConsolidator(db, default_tz=TZ),
    )

    result = _turn(graph, seeded)

    assert result.answer  # the turn still answered
    assert any(ANALYZE_SERIES in e for e in result.errors)
    assert _insight_count(db, seeded) == 0


def test_a_bad_metric_is_reported_not_raised(db, seeded):
    provider = FakeModelProvider(plan_calls=[_call(ANALYZE_SERIES, metric="horoscope")])
    graph = _graph(db, provider)

    result = _turn(graph, seeded)

    assert result.answer
    assert any(ANALYZE_SERIES in e for e in result.errors)
    assert result.consolidation == []


def test_an_unconfigured_service_degrades_honestly(db, seeded):
    """No consolidator wired means the tool cannot run — and the turn says so rather than
    pretending nothing was asked."""
    provider = FakeModelProvider(plan_calls=[_call(ANALYZE_SERIES, metric="protein_g")])
    graph = _graph(db, provider, consolidation=False)

    result = _turn(graph, seeded)

    assert result.answer
    assert any("not configured" in e for e in result.errors)


# ══ turn-local objects stay off the checkpoint (M5-1) ══════════════════════════════════
def test_consolidation_output_rides_the_carrier_not_graph_state():
    """**M5-1.** Heavy, turn-local artifacts travel on the per-invocation carrier; the
    checkpointed channel set is unchanged by this milestone."""
    from agent.graph import STATE_CHANNELS

    assert "consolidation" in TurnCarrier.__dataclass_fields__
    assert "consolidation" not in STATE_CHANNELS
