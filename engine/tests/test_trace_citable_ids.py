"""The trace carries its own citable set (ADR-14.8, glass-box-architecture.md §4.2).

Pure: no database, no model, no clock. A failure here names a contract, never an environment.

The load-bearing test in this module is ``test_aggregated_citation_is_not_a_false_positive``.
It is the *reason* §4.2 exists, and without it the fix is unproven rather than tested.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from engine.assembly import RetrievalOutcome, assemble
from engine.retrieval import AggregateBucket, AggregateResult, AggregateSpec
from engine.trace import RetrievalStep

UTC = timezone.utc
NOW = datetime(2026, 7, 24, 6, 40, tzinfo=UTC)


def _aggregate_outcome(evidence_ids):
    """One aggregate whose bucket was computed from ``evidence_ids``.

    Deliberately the *only* thing in the turn: an aggregate contributes citable IDs while
    contributing no evidence snapshots, which is exactly the asymmetry ADR-14.8 is about.
    """
    spec = AggregateSpec(
        metric="protein_g",
        start=datetime(2026, 5, 1, tzinfo=UTC),
        end=datetime(2026, 6, 1, tzinfo=UTC),
        tz="Asia/Kolkata",
        agg="avg",
        group_by="week",
    )
    result = AggregateResult(
        spec=spec,
        buckets=(
            AggregateBucket(
                bucket="2026-05-04",
                value=46.0,
                n=len(evidence_ids),
                evidence_ids=tuple(evidence_ids),
            ),
        ),
    )
    step = RetrievalStep(family="aggregate", sql="SELECT 1", params={}, row_count=len(evidence_ids))
    return RetrievalOutcome(result=result, step=step)


def test_trace_carries_the_contexts_citable_set() -> None:
    """One definition of "citable", shared by the narrator's prompt and the validator."""
    contributors = [uuid4() for _ in range(3)]
    context, trace = assemble("how much protein in May?", [_aggregate_outcome(contributors)])

    assert trace.citable_ids == context.citable_ids()


def test_aggregated_citation_is_not_a_false_positive() -> None:
    """The defect ADR-14.8 records: a *valid* citation of an aggregated meal must not be
    flagged invalid by a reader that only has the trace.

    Assembly is pure (ADR-14.7), so an aggregate's contributing rows are never hydrated into
    ``trace.evidence``. A validator reading ``evidence`` alone would reject every one of them.
    """
    contributors = [uuid4() for _ in range(3)]
    _, trace = assemble("how much protein in May?", [_aggregate_outcome(contributors)])

    evidence_ids = {snapshot.id for snapshot in trace.evidence}
    for contributor in contributors:
        assert contributor not in evidence_ids, "precondition: aggregates hydrate no snapshots"
        assert contributor in trace.citable_ids, "ADR-14.8 regression: valid citation would fail"


def test_citable_ids_survive_json_round_trip() -> None:
    """The persisted trace is what the API serves (I-29), so the set has to survive JSONB."""
    contributors = [uuid4() for _ in range(3)]
    _, trace = assemble("how much protein in May?", [_aggregate_outcome(contributors)])

    payload = trace.to_json()

    assert payload["citable_ids"] == sorted(str(i) for i in trace.citable_ids)
    assert payload["citable_ids"] == sorted(payload["citable_ids"]), "sorted keeps JSONB stable"


def test_empty_turn_has_an_empty_citable_set() -> None:
    """An honest empty answer, not a missing field: the key is always present."""
    _, trace = assemble("hello", [])

    assert trace.citable_ids == frozenset()
    assert trace.to_json()["citable_ids"] == []
