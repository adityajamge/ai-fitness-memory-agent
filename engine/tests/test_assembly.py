"""Context assembly + ranking tests (M3 — 12-test-plan.md `engine/trace` property block +
the ranking/merge commitments of 06-retrieval-strategy.md).

Pure-logic tests: retrieval results are constructed directly (no DB, no model), which is
exactly the point — assembly is a deterministic function of its inputs. The flagship
property is ADR-12's: assembling anything yields a trace, and the trace covers the inputs.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from engine.assembly import RetrievalOutcome, assemble
from engine.retrieval import (
    AggregateBucket,
    AggregateResult,
    AggregateSpec,
    CountResult,
    CountSpec,
    LookupResult,
    LookupSpec,
    RecallHit,
    RecallResult,
    RecallSpec,
    TimelineResult,
    TimelineSpec,
)
from engine.trace import EvidenceSnapshot, EvidenceTrace, RetrievalStep

UTC = timezone.utc
T0 = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
FIXED_TRACE = UUID("00000000-0000-0000-0000-0000000000aa")
FIXED_AT = datetime(2026, 7, 23, 9, 0, tzinfo=UTC)
START = datetime(2026, 7, 1, tzinfo=UTC)
END = datetime(2026, 8, 1, tzinfo=UTC)


# ── fixture factories (direct construction, no DB) ────────────────────────────────────
def _step(family: str) -> RetrievalStep:
    return RetrievalStep(family=family, sql=f"SELECT -- {family}", params={}, row_count=0)


def _snap(
    *,
    mem_id: UUID | None = None,
    type_: str = "note",
    event_time: datetime = T0,
    confidence: float = 0.9,
    provenance: str = "live",
    summary: str = "s",
) -> EvidenceSnapshot:
    return EvidenceSnapshot(
        id=mem_id or uuid4(),
        type=type_,
        event_time=event_time,
        confidence=confidence,
        provenance=provenance,
        summary=summary,
    )


def _recall(*hits: tuple[EvidenceSnapshot, float]) -> RetrievalOutcome:
    recall_hits = tuple(
        RecallHit(
            id=s.id,
            type=s.type,
            event_time=s.event_time,
            confidence=s.confidence,
            provenance=s.provenance,
            summary=s.summary,
            distance=d,
        )
        for s, d in hits
    )
    return RetrievalOutcome(
        result=RecallResult(spec=RecallSpec(query="q"), hits=recall_hits), step=_step("recall")
    )


def _timeline(*snaps: EvidenceSnapshot) -> RetrievalOutcome:
    return RetrievalOutcome(
        result=TimelineResult(spec=TimelineSpec(start=START, end=END), entries=tuple(snaps)),
        step=_step("timeline"),
    )


def _lookup(*snaps: EvidenceSnapshot) -> RetrievalOutcome:
    return RetrievalOutcome(
        result=LookupResult(spec=LookupSpec(type="meal"), entries=tuple(snaps)),
        step=_step("lookup"),
    )


def _aggregate(evidence_ids: tuple[UUID, ...], value: float = 100.0) -> RetrievalOutcome:
    bucket = AggregateBucket(
        bucket=None, value=value, n=len(evidence_ids), evidence_ids=evidence_ids
    )
    spec = AggregateSpec(metric="protein_g", start=START, end=END, tz="UTC")
    return RetrievalOutcome(
        result=AggregateResult(spec=spec, buckets=(bucket,)), step=_step("aggregate")
    )


def _count(evidence_ids: tuple[UUID, ...]) -> RetrievalOutcome:
    spec = CountSpec(type="workout", start=START, end=END)
    result = CountResult(spec=spec, n=len(evidence_ids), evidence_ids=evidence_ids)
    return RetrievalOutcome(result=result, step=_step("lookup"))


def _assemble(outcomes, **kw):
    return assemble("test question", outcomes, trace_id=FIXED_TRACE, assembled_at=FIXED_AT, **kw)


# ── ADR-12 property: no context without a covering trace ──────────────────────────────
def test_assembly_always_yields_a_trace_covering_its_inputs() -> None:
    a, b, c = _snap(summary="a"), _snap(summary="b"), _snap(summary="c")
    outcomes = [_recall((a, 0.2)), _timeline(b), _lookup(c), _aggregate((uuid4(),))]

    context, trace = _assemble(outcomes)

    assert isinstance(trace, EvidenceTrace)
    # one step per outcome, in order
    assert len(trace.retrieval_steps) == len(outcomes)
    assert [s.family for s in trace.retrieval_steps] == [
        "recall",
        "timeline",
        "lookup",
        "aggregate",
    ]
    # evidence covers exactly the row-returning inputs (recall + timeline + lookup)
    assert {e.id for e in trace.evidence} == {a.id, b.id, c.id}
    # ranking covers exactly the evidence set
    assert {r.memory_id for r in trace.ranking} == {a.id, b.id, c.id}
    # timeline field is populated from the timeline family only
    assert {t.id for t in trace.timeline} == {b.id}
    assert trace.insights == ()


def test_empty_input_still_produces_a_valid_empty_trace() -> None:
    context, trace = _assemble([])
    assert context.is_empty
    assert context.memories == ()
    assert context.omitted_count == 0
    assert trace.retrieval_steps == ()
    assert trace.evidence == ()
    assert trace.ranking == ()
    assert trace.trace_id == FIXED_TRACE  # a trace exists regardless
    assert trace.to_json()["evidence"] == []


# ── determinism ───────────────────────────────────────────────────────────────────────
def test_ranking_is_deterministic_across_calls() -> None:
    snaps = [_snap(event_time=T0 + timedelta(days=i), summary=f"m{i}") for i in range(5)]
    outcomes = [_recall(*[(s, 0.1 * i) for i, s in enumerate(snaps)])]

    _, trace_a = _assemble(outcomes)
    _, trace_b = _assemble(outcomes)
    scores_a = [(r.memory_id, r.score) for r in trace_a.ranking]
    scores_b = [(r.memory_id, r.score) for r in trace_b.ranking]
    assert scores_a == scores_b


def test_scores_are_independent_of_trace_id_and_timestamp() -> None:
    s = _snap()
    _, t1 = assemble("q", [_recall((s, 0.3))], trace_id=uuid4(), assembled_at=FIXED_AT)
    _, t2 = assemble("q", [_recall((s, 0.3))], trace_id=uuid4(), assembled_at=T0)
    assert t1.ranking[0].score == t2.ranking[0].score


# ── dedup + merge (the Chicken scenario, and same-ID across tools) ────────────────────
def test_same_memory_from_two_tools_is_one_candidate_with_max_relevance() -> None:
    shared = _snap(summary="shared")
    # recall matches it weakly (far distance → low relevance); lookup matches exactly (1.0)
    context, trace = _assemble([_recall((shared, 1.4)), _lookup(shared)])

    assert len(trace.evidence) == 1
    assert len(trace.ranking) == 1
    assert trace.ranking[0].relevance == pytest.approx(1.0)  # structured match won


def test_distinct_ids_from_two_tools_are_both_kept_ordered_by_time() -> None:
    # The Chicken merge: structured lookup finds the OLD exact "Chicken"; recall finds the
    # NEWER "Grilled Chicken". Both survive as distinct evidence; event_time is preserved so
    # the narrator can resolve "last" by recency.
    old_exact = _snap(mem_id=uuid4(), event_time=T0, summary="Chicken")
    new_semantic = _snap(
        mem_id=uuid4(), event_time=T0 + timedelta(days=1), summary="Grilled Chicken"
    )
    context, trace = _assemble([_lookup(old_exact), _recall((new_semantic, 0.4))])

    ids = {e.id for e in trace.evidence}
    assert ids == {old_exact.id, new_semantic.id}
    latest = max(context.memories, key=lambda m: m.event_time)
    assert latest.id == new_semantic.id  # recency is recoverable from the context


# ── the ranking axes ──────────────────────────────────────────────────────────────────
def test_closer_recall_hit_outranks_farther_one() -> None:
    near = _snap(mem_id=uuid4(), summary="near")
    far = _snap(mem_id=uuid4(), summary="far")
    _, trace = _assemble([_recall((near, 0.1), (far, 1.6))])
    assert trace.evidence[0].id == near.id
    assert trace.ranking[0].relevance > trace.ranking[1].relevance


def test_relevance_from_distance_is_monotone_and_bounded() -> None:
    exact = _snap(mem_id=uuid4())
    orthogonal = _snap(mem_id=uuid4())
    opposite = _snap(mem_id=uuid4())
    _, trace = _assemble([_recall((exact, 0.0), (orthogonal, math.sqrt(2)), (opposite, 2.0))])
    by_id = {r.memory_id: r.relevance for r in trace.ranking}
    assert by_id[exact.id] == pytest.approx(1.0)
    assert by_id[orthogonal.id] == pytest.approx(1 - math.sqrt(2) / 2)
    assert by_id[opposite.id] == pytest.approx(0.0)


def test_reconstructed_ranks_below_live_at_equal_stated_confidence() -> None:
    live = _snap(mem_id=uuid4(), provenance="live", confidence=0.8, summary="live")
    recon = _snap(mem_id=uuid4(), provenance="reconstructed", confidence=0.8, summary="recon")
    # same relevance + same time → only provenance differs
    _, trace = _assemble([_lookup(live, recon)])
    ranked_ids = [e.id for e in trace.evidence]
    assert ranked_ids[0] == live.id
    by_id = {r.memory_id: r.confidence for r in trace.ranking}
    assert by_id[live.id] > by_id[recon.id]


def test_low_confidence_ranks_below_high_confidence() -> None:
    high = _snap(mem_id=uuid4(), confidence=0.95, summary="high")
    low = _snap(mem_id=uuid4(), confidence=0.30, summary="low")
    _, trace = _assemble([_lookup(high, low)])
    assert trace.evidence[0].id == high.id


def test_newer_memory_ranks_above_older_all_else_equal() -> None:
    older = _snap(mem_id=uuid4(), event_time=T0, summary="old")
    newer = _snap(mem_id=uuid4(), event_time=T0 + timedelta(days=10), summary="new")
    _, trace = _assemble([_lookup(older, newer)])
    assert trace.evidence[0].id == newer.id
    by_id = {r.memory_id: r.recency for r in trace.ranking}
    assert by_id[newer.id] == pytest.approx(1.0)
    assert by_id[older.id] == pytest.approx(0.0)


def test_insight_tier_outranks_episodic_at_equal_other_axes() -> None:
    episodic = _snap(mem_id=uuid4(), type_="meal", summary="meal")
    insight = _snap(mem_id=uuid4(), type_="insight", summary="hypothesis")
    # both arrive via lookup (structured relevance 1.0) at the same time → only tier differs
    _, trace = _assemble([_lookup(episodic, insight)])
    by_id = {r.memory_id: r.tier for r in trace.ranking}
    assert by_id[insight.id] > by_id[episodic.id]
    assert trace.evidence[0].id == insight.id


# ── diversity cap + budget (apply to context, never to trace evidence) ────────────────
def test_diversity_cap_limits_one_type_in_context_but_not_in_trace() -> None:
    meals = [_snap(mem_id=uuid4(), type_="meal", summary=f"meal{i}") for i in range(8)]
    context, trace = _assemble([_lookup(*meals)], per_type_cap=3, max_memories=12)
    assert len([m for m in context.memories if m.type == "meal"]) == 3
    assert context.omitted_count == 5
    assert len(trace.evidence) == 8  # the glass box still shows everything retrieved


def test_budget_caps_raw_events_but_never_aggregates() -> None:
    notes = [_snap(mem_id=uuid4(), summary=f"n{i}") for i in range(20)]
    agg_ids = tuple(uuid4() for _ in range(3))
    context, trace = _assemble(
        [_lookup(*notes), _aggregate(agg_ids)], max_memories=5, per_type_cap=99
    )
    assert len(context.memories) == 5
    assert context.omitted_count == 15
    assert len(context.aggregates) == 1  # aggregate is exempt from the raw-event budget
    assert len(trace.evidence) == 20


# ── aggregates / counts pass-through + citable IDs ────────────────────────────────────
def test_aggregates_and_counts_pass_through_and_are_citable() -> None:
    agg_ids = tuple(uuid4() for _ in range(3))
    count_ids = tuple(uuid4() for _ in range(2))
    mem = _snap(mem_id=uuid4())
    context, _ = _assemble([_aggregate(agg_ids), _count(count_ids), _lookup(mem)])

    assert len(context.aggregates) == 1
    assert len(context.counts) == 1
    citable = context.citable_ids()
    assert set(agg_ids) <= citable
    assert set(count_ids) <= citable
    assert mem.id in citable


def test_context_with_only_aggregates_is_not_empty() -> None:
    context, trace = _assemble([_aggregate((uuid4(),))])
    assert not context.is_empty
    assert context.memories == ()
    assert len(trace.retrieval_steps) == 1


# ── trace serializes end-to-end (the shape Phase 6 persists) ──────────────────────────
def test_assembled_trace_is_json_serializable() -> None:
    import json

    s = _snap()
    _, trace = _assemble([_recall((s, 0.2)), _aggregate((uuid4(),))])
    payload = trace.to_json()
    assert json.loads(json.dumps(payload)) == payload
    assert payload["assembled_at"] == FIXED_AT.isoformat()
