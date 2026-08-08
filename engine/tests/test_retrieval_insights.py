"""The insight builder family + trace lineage (Phase 5 M5b, §4.10).

The sixth family 06 reserved. It exists because an ``EvidenceSnapshot`` is **deliberately
payload-free** (ADR-12 — a trace references memories, it never copies payloads) while an
insight's lineage lives *in* its payload: retrieving insights through the snapshot-shaped
families would either lose the lineage or breach that boundary. The identical conflict was hit
in the 2026-07-29 manual validation over meal quantities and correctly refused; a dedicated
result type resolves it with nothing bent.

Under test:

* **I-17** — the family is read-only. Deriving a claim is ``analyze_series``; looking one up is
  a question, and a question must not write.
* **ADR-14.7** — ``assemble()`` stays a pure function: it maps what the outcome brought and
  performs no I/O.
* **§4.10 / Q1** — insight IDs join the citable surface; an insight's *own* ``evidence_ids``
  deliberately do not, because that is T7's decision to make.
"""

from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from engine import assembly as assembly_module
from engine.assembly import RetrievalOutcome, assemble
from engine.memory import Memory
from engine.repository import insert_memories
from engine.retrieval import (
    InsightSpec,
    RecallHit,
    RecallResult,
    RecallSpec,
    RetrievalSpecError,
    TimelineResult,
    TimelineSpec,
    lookup_insights,
)
from engine.tests.dbcleanup import new_user
from engine.trace import EvidenceSnapshot, RetrievalStep

TZ = "Asia/Kolkata"
IST = timezone(timedelta(hours=5, minutes=30))


def _at(day: str) -> datetime:
    return datetime.fromisoformat(f"{day}T12:00:00").replace(tzinfo=IST)


def _payload(**overrides) -> dict:
    base = {
        "kind": "level_shift",
        "hypothesis": "protein rose from ~45 to ~83 g/day starting 2026-06-23",
        "series_metric": "protein_g",
        "series_kind": "behavioural",
        "window_start": "2026-06-15T00:00:00+05:30",
        "window_end": "2026-06-30T00:00:00+05:30",
        "pre_value": 45.0,
        "post_value": 83.0,
        "evidence_ids": [str(uuid4()), str(uuid4())],
        "evidence_count": 16,
        "effect": 0.844, "coverage": 1.0, "specificity": 1.0, "pattern_strength": 0.844,
        "fingerprint": "fp-1",
    }
    base.update(overrides)
    return base


def _seed_insight(db, user_id: UUID, *, status: str = "active", day: str = "2026-06-30",
                  **overrides) -> UUID:
    payload = _payload(**overrides)
    with db.transaction() as cur:
        (insight_id,) = insert_memories(cur, [Memory(
            user_id=user_id, event_time=_at(day), tz=TZ, type="insight",
            source="consolidation", provenance="live", confidence=1.0,
            summary=payload["hypothesis"], payload=payload, status=status,
        )])
    return insight_id


def _lookup(db, user_id: UUID, spec: InsightSpec | None = None):
    with db.transaction() as cur:
        return lookup_insights(cur, user_id, spec or InsightSpec())


def _snap(memory_id: UUID | None = None, type_: str = "meal") -> EvidenceSnapshot:
    return EvidenceSnapshot(
        id=memory_id or uuid4(), type=type_, event_time=_at("2026-06-20"),
        confidence=1.0, provenance="live", summary="a memory",
    )


def _step(family: str = "insight") -> RetrievalStep:
    return RetrievalStep(family=family, sql="SELECT 1", params={}, row_count=1)


def _timeline(entries: tuple[EvidenceSnapshot, ...]) -> TimelineResult:
    return TimelineResult(
        spec=TimelineSpec(start=_at("2026-06-01"), end=_at("2026-07-01")), entries=entries
    )


def _recall(memory_id: UUID) -> RecallResult:
    """The same insight arriving through the payload-free semantic path."""
    return RecallResult(
        spec=RecallSpec(query="anything noticed?"),
        hits=(RecallHit(
            id=memory_id, type="insight", event_time=_at("2026-06-30"), confidence=1.0,
            provenance="live", summary="protein rose", distance=0.2,
        ),),
    )


# ══ the family ═════════════════════════════════════════════════════════════════════════
def test_it_returns_the_lineage_a_snapshot_cannot_carry(db, user_id):
    """The reason this family exists at all."""
    insight_id = _seed_insight(db, user_id)
    result, step = _lookup(db, user_id)

    assert len(result.entries) == 1
    entry = result.entries[0]
    assert entry.id == insight_id
    assert entry.hypothesis.startswith("protein rose")
    assert len(entry.evidence_ids) == 2
    assert all(isinstance(i, UUID) for i in entry.evidence_ids)
    assert entry.evidence_count == 16  # the TRUE total, beside the capped lineage
    assert entry.pattern_strength == pytest.approx(0.844)
    assert (entry.effect, entry.coverage, entry.specificity) == (0.844, 1.0, 1.0)
    assert step.family == "insight"


def test_an_empty_result_is_defined_not_an_error(db, user_id):
    result, step = _lookup(db, user_id)
    assert result.is_empty
    assert step.row_count == 0


def test_it_defaults_to_active_and_hides_retracted_claims(db, user_id):
    """Same posture as every other builder: a withdrawn claim must not reappear in an answer
    by accident."""
    active = _seed_insight(db, user_id, fingerprint="a")
    _seed_insight(db, user_id, status="retracted", fingerprint="b")
    _seed_insight(db, user_id, status="superseded", fingerprint="c")

    result, _ = _lookup(db, user_id)
    assert [e.id for e in result.entries] == [active]


def test_retracted_claims_are_reachable_deliberately(db, user_id):
    """ADR-9: the engine's history of being wrong is itself memory, and the glass box is
    supposed to be able to show it — as an explicit request, never a default."""
    retracted = _seed_insight(db, user_id, status="retracted")
    result, _ = _lookup(db, user_id, InsightSpec(status="retracted"))
    assert [e.id for e in result.entries] == [retracted]
    assert result.entries[0].status == "retracted"


def test_it_filters_by_series_and_kind(db, user_id):
    protein = _seed_insight(db, user_id, fingerprint="p")
    _seed_insight(
        db, user_id, series_metric="body_fat_pct", series_kind="outcome",
        kind="intervention_outcome", fingerprint="b",
    )

    by_metric, _ = _lookup(db, user_id, InsightSpec(metric="protein_g"))
    assert [e.id for e in by_metric.entries] == [protein]

    by_kind, _ = _lookup(db, user_id, InsightSpec(kind="intervention_outcome"))
    assert [e.series_metric for e in by_kind.entries] == ["body_fat_pct"]


def test_results_are_newest_first_and_limited(db, user_id):
    for n, day in enumerate(("2026-04-30", "2026-05-31", "2026-06-30")):
        _seed_insight(db, user_id, day=day, fingerprint=f"f{n}")

    result, _ = _lookup(db, user_id, InsightSpec(limit=2))
    assert len(result.entries) == 2
    assert result.entries[0].event_time > result.entries[1].event_time


def test_it_is_user_scoped(db, user_id):
    stranger = new_user()
    _seed_insight(db, stranger)
    result, _ = _lookup(db, user_id)
    assert result.is_empty


@pytest.mark.parametrize(
    "kwargs",
    [
        {"metric": "horoscope"},
        {"metric": "carbs_g"},          # aggregatable, but not a consolidatable series
        {"kind": "vibes"},
        {"status": "deleted"},
        {"limit": 0},
        {"limit": 999},
    ],
)
def test_bad_slots_die_above_the_database(kwargs):
    """ADR-14.11: every planner mistake fails before any SQL is composed."""
    with pytest.raises(RetrievalSpecError):
        InsightSpec(**kwargs)


# ══ I-17 — the family is read-only ═════════════════════════════════════════════════════
def test_the_lookup_writes_nothing(db, user_id):
    """**I-17.** Looking up a claim is a question. Deriving one is `analyze_series`, which the
    graph dispatches outside the retrieve transaction precisely because it writes."""
    _seed_insight(db, user_id)
    with db.transaction() as cur:
        cur.execute("SELECT count(*) AS n FROM memories WHERE user_id = %s", [user_id])
        before = int(cur.fetchone()["n"])

    for _ in range(3):
        _lookup(db, user_id)

    with db.transaction() as cur:
        cur.execute("SELECT count(*) AS n FROM memories WHERE user_id = %s", [user_id])
        assert int(cur.fetchone()["n"]) == before


def test_the_family_contains_no_write_verb():
    """Structural I-17: the builder cannot write, because it has no statement that could."""
    source = inspect.getsource(lookup_insights)
    for verb in ("INSERT", "UPDATE", "DELETE", "insert_memories", "mark_"):
        assert verb not in source


# ══ assemble() — lineage into the trace, ids into the citable surface ══════════════════
def test_assemble_populates_trace_insights_with_lineage(db, user_id):
    _seed_insight(db, user_id)
    result, step = _lookup(db, user_id)

    context, trace = assemble("what have you noticed?", [RetrievalOutcome(result, step)])

    assert len(trace.insights) == 1
    ref = trace.insights[0]
    assert ref.hypothesis.startswith("protein rose")
    assert ref.evidence_ids == result.entries[0].evidence_ids
    assert ref.to_json()["evidence_ids"]  # renders for the glass box


def test_assemble_threads_pattern_strength_and_no_retraction_when_none_was_written(db, user_id):
    """Phase 6 M8: the text lineage list (DESIGN.md §9 "click an insight") needs both the
    strength score and the retraction sentence. No condition was written here, so the rendered
    field must be honestly absent rather than a placeholder."""
    _seed_insight(db, user_id)
    result, step = _lookup(db, user_id)

    _, trace = assemble("what have you noticed?", [RetrievalOutcome(result, step)])

    ref = trace.insights[0]
    assert ref.pattern_strength == pytest.approx(0.844)
    assert ref.retraction is None
    assert ref.to_json()["pattern_strength"] == pytest.approx(0.844)
    assert ref.to_json()["retraction"] is None


def test_assemble_renders_the_retraction_condition_as_prose(db, user_id):
    """The trace carries a sentence, never the structured condition (rule 16) — and it is the
    same sentence ``render_retraction_condition`` produces directly, so display and evaluation
    can never quietly disagree (ADR-13.11)."""
    from engine.insights import render_retraction_condition
    from engine.types import RetractionCondition

    condition = {
        "metric": "protein_g",
        "direction": "falling",
        "window_days": 7,
        "min_count": 2,
        "threshold": 45.0,
    }
    _seed_insight(db, user_id, retraction_condition=condition)
    result, step = _lookup(db, user_id)

    _, trace = assemble("what have you noticed?", [RetrievalOutcome(result, step)])

    ref = trace.insights[0]
    expected = render_retraction_condition(RetractionCondition.model_validate(condition))
    assert ref.retraction == expected
    assert "protein" in ref.retraction.lower()


def test_insight_ids_are_citable(db, user_id):
    """§4.10: the narrator may cite the claim it was shown."""
    insight_id = _seed_insight(db, user_id)
    result, step = _lookup(db, user_id)

    context, _ = assemble("q", [RetrievalOutcome(result, step)])

    assert insight_id in context.citable_ids()


def test_an_insights_own_evidence_ids_are_not_citable(db, user_id):
    """**Q1 stays open.** Whether the narrator may cite the rows *underneath* a claim is T7's
    decision, alongside ADR-14.8. A surface is far easier to widen later than to narrow, so
    this milestone does not pre-empt it."""
    _seed_insight(db, user_id)
    result, step = _lookup(db, user_id)

    context, _ = assemble("q", [RetrievalOutcome(result, step)])

    lineage = set(result.entries[0].evidence_ids)
    assert lineage and not (lineage & context.citable_ids())


def test_insights_bypass_the_raw_event_budget(db, user_id):
    """Like aggregates and counts, they are compact computed facts — an insight that answers
    the question is the last thing that should be crowded out by the events it summarises."""
    _seed_insight(db, user_id)
    result, step = _lookup(db, user_id)
    crowd = RetrievalOutcome(_timeline(tuple(_snap() for _ in range(40))), _step("timeline"))

    context, _ = assemble("q", [RetrievalOutcome(result, step), crowd], max_memories=3)

    assert len(context.memories) == 3       # the budget still bites raw events
    assert len(context.insights) == 1       # ...and never the insight


def test_the_richer_representation_wins_when_an_insight_arrives_twice(db, user_id):
    """06: one memory is one candidate. An insight can arrive as a payload-free snapshot via
    recall *and* as a full row via this family; only the latter carries lineage."""
    insight_id = _seed_insight(db, user_id)
    result, step = _lookup(db, user_id)
    also_recalled = RetrievalOutcome(_recall(insight_id), _step("recall"))

    context, trace = assemble("q", [RetrievalOutcome(result, step), also_recalled])

    assert [i.id for i in context.insights] == [insight_id]
    assert insight_id not in {m.id for m in context.memories}
    assert insight_id not in {e.id for e in trace.evidence}


def test_duplicate_insight_rows_collapse(db, user_id):
    _seed_insight(db, user_id)
    result, step = _lookup(db, user_id)
    context, trace = assemble("q", [RetrievalOutcome(result, step),
                                    RetrievalOutcome(result, step)])
    assert len(context.insights) == 1
    assert len(trace.insights) == 1


def test_no_insights_still_yields_an_honest_empty_tuple(db, user_id):
    result, step = _lookup(db, user_id)
    context, trace = assemble("q", [RetrievalOutcome(result, step)])
    assert trace.insights == ()
    assert context.insights == ()
    assert context.is_empty


# ══ ADR-14.7 — assemble() is still pure ════════════════════════════════════════════════
def test_assembly_performs_no_io():
    """**ADR-14.7.** The module that ranks evidence must stay fixture-testable and
    deterministic; it maps what the outcome brought and fetches nothing."""
    source = inspect.getsource(assembly_module)
    for forbidden in ("psycopg", "db.transaction", "cursor", "execute(", "import engine.db"):
        assert forbidden not in source


def test_assemble_is_deterministic_over_insights(db, user_id):
    _seed_insight(db, user_id)
    result, step = _lookup(db, user_id)
    outcomes = [RetrievalOutcome(result, step)]

    first, _ = assemble("q", outcomes, trace_id=UUID(int=7), assembled_at=_at("2026-07-01"))
    second, _ = assemble("q", outcomes, trace_id=UUID(int=7), assembled_at=_at("2026-07-01"))
    assert first == second
