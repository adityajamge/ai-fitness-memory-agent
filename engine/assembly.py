"""Context assembly + ranking — the deterministic merge point (03-memory-engine.md §5,
06-retrieval-strategy.md "ranking & assembly").

``assemble`` is the first consumer of the M1–M2 builders. It takes the outputs of one or
more retrieval tool calls and produces **one** ranked, deduplicated, budgeted
``ContextBlock`` for the narrator **and** the ``EvidenceTrace`` that justifies it — always
as a pair (ADR-12: if context was assembled, a trace exists, by construction). There is no
code path that returns a context without a trace.

Determinism boundary (the load-bearing invariant): this module performs **no language
interpretation**. It does arithmetic over typed fields (distances, confidences, event
times, memory types) only. The ``question`` string is carried into the context and trace
for display and is never parsed. Same inputs → same ranking → same trace, every time; the
per-candidate scores are recorded in ``EvidenceTrace.ranking`` so "why the engine picked
these memories" is itself glass-box material.

Two views of the retrieved evidence, deliberately separated:
  * ``EvidenceTrace.evidence`` — **everything** the tools returned as rows (deduped),
    ordered by score. The glass box shows all of it; ``ranking`` explains what was cut.
  * ``ContextBlock.memories`` — the budget-limited, diversity-capped subset actually handed
    to the narrator. Context optimization lives here; the trace stays complete.

Aggregates, counts, and derived insights are computed facts, compact by nature (06): they always
pass into the context and are never subject to the raw-event budget. An insight that already
answers the question is the last thing that should be crowded out by the events it summarises —
that is 06's tier axis, applied at the budget rather than only at the score. Their contributing
memory IDs are preserved for citation (``ContextBlock.citable_ids``). Assembly is a **pure
function** of its inputs — it never touches the database; hydrating an aggregate's contributing
rows into full snapshots is Phase 6's batch-fetch (T16), not M3's job.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import UUID, uuid4

from engine.insights import render_retraction_condition
from engine.retrieval import (
    AggregateResult,
    CountResult,
    InsightResult,
    InsightRow,
    LookupResult,
    RecallResult,
    TimelineResult,
)
from engine.trace import (
    EvidenceSnapshot,
    EvidenceTrace,
    InsightRef,
    RankingEntry,
    RetrievalStep,
)

# ── ranking model (documented heuristic — exact weights are impl detail per 06; the axes
# and their directions are the architectural commitment) ───────────────────────────────
_W_RELEVANCE = 0.40  # match quality: vector proximity (semantic) or exact filter (structured)
_W_CONFIDENCE = 0.25  # memory confidence, with provenance folded in (reconstructed ranks lower)
_W_RECENCY = 0.20  # temporal proximity within the retrieved set
_W_TIER = 0.15  # a derived insight that already answers the question outranks raw events

_STRUCTURED_RELEVANCE = 1.0  # an exact filter/containment match is certain, by definition
_MAX_L2 = 2.0  # unit vectors: L2 distance ∈ [0, 2]; relevance = 1 - d/2 (monotone, [0,1])

# Provenance factor multiplies the confidence axis: a reconstructed memory of equal stated
# confidence ranks below a live one (06 — "the answer can hedge: 'around early May'").
_PROVENANCE_FACTOR = {"live": 1.0, "reconstructed": 0.85}
_PROVENANCE_DEFAULT = 0.85

_TIER_SCORE = {"insight": 1.0}  # tier-2 derived memory (Phase 5+)
_EPISODIC_TIER = 0.5  # tier-1 typed event (all Phase 3 memories)

# Context budget (raw events only; aggregates/counts are exempt). Impl detail, documented.
_DEFAULT_MAX_MEMORIES = 12  # narration budget: raw event rows handed to the model
_DEFAULT_PER_TYPE_CAP = 5  # diversity: no single type may fill the budget with near-dupes


@dataclass(frozen=True, slots=True)
class ContextBlock:
    """The budgeted, ranked evidence handed to the narrator. Memory IDs are preserved on
    every datum so citations survive into the UI (06). Rendering to prompt text happens at
    the narrate boundary (M5, decision D-5); this stays structured."""

    question: str
    aggregates: tuple[AggregateResult, ...]
    counts: tuple[CountResult, ...]
    memories: tuple[EvidenceSnapshot, ...]  # ranked desc, diversity-capped, budget-limited
    omitted_count: int  # retrieved rows that ranking/budget kept out of the narration view
    #: Derived tier-2 memories the insight family returned, with their lineage (§4.10). Like
    #: aggregates and counts they are compact computed facts, so they always pass through and
    #: are never subject to the raw-event budget — an insight that answers the question is the
    #: last thing that should be crowded out by the events it summarises (06, tier axis).
    insights: tuple[InsightRow, ...] = ()
    #: A short rendering of the user's profile (name/goal/targets/dietary notes), attached by
    #: the graph **after** ``assemble()`` returns (ADR-17.5) — never set by this module, which
    #: stays DB-free by construction. Prose context for the narrator, not evidence: deliberately
    #: excluded from ``citable_ids()`` below, since a target is not a memory the narrator may
    #: cite by id.
    profile_note: str | None = None

    @property
    def is_empty(self) -> bool:
        return not (self.aggregates or self.counts or self.memories or self.insights)

    def citable_ids(self) -> frozenset[UUID]:
        """Every memory ID the narrator is allowed to cite from this context: the budgeted
        memories, the contributing IDs of every aggregate/count, and each participating
        insight. Phase 6 (T7) citation validation checks the answer's markers against this set
        (honest scope, ADR-13.13).

        An insight's **own** ``evidence_ids`` are deliberately **not** added. Whether the
        narrator may cite the rows underneath a claim it is citing is open question Q1, handed
        to T7 alongside ADR-14.8; widening the surface here would pre-empt that decision, and a
        surface is far easier to widen later than to narrow.
        """
        ids: set[UUID] = {m.id for m in self.memories}
        for agg in self.aggregates:
            for bucket in agg.buckets:
                ids.update(bucket.evidence_ids)
        for cnt in self.counts:
            ids.update(cnt.evidence_ids)
        ids.update(insight.id for insight in self.insights)
        return frozenset(ids)


@dataclass(frozen=True, slots=True)
class RetrievalOutcome:
    """One executed tool call: its typed result plus the RetrievalStep it emitted. The M5
    graph builds these from planner tool calls; assembly merges a list of them."""

    result: (
        AggregateResult | RecallResult | TimelineResult | LookupResult | CountResult
        | InsightResult
    )
    step: RetrievalStep


@dataclass
class _Candidate:
    """A unique memory under consideration, accumulating the best relevance seen across
    however many tools surfaced it (dedup by memory ID)."""

    snapshot: EvidenceSnapshot
    relevance: float
    _score: float = field(default=0.0)


def assemble(
    question: str,
    outcomes: list[RetrievalOutcome],
    *,
    max_memories: int = _DEFAULT_MAX_MEMORIES,
    per_type_cap: int = _DEFAULT_PER_TYPE_CAP,
    trace_id: UUID | None = None,
    assembled_at: datetime | None = None,
) -> tuple[ContextBlock, EvidenceTrace]:
    """Merge retrieval outcomes into one ranked context and its evidence trace.

    ``trace_id`` / ``assembled_at`` are injectable for deterministic tests; the *scores*
    never depend on either, so ranking is reproducible regardless of them.
    """
    aggregates: list[AggregateResult] = []
    counts: list[CountResult] = []
    insights: list[InsightRow] = []
    timeline_entries: list[EvidenceSnapshot] = []
    candidates: dict[UUID, _Candidate] = {}
    steps: list[RetrievalStep] = [o.step for o in outcomes]

    for outcome in outcomes:
        result = outcome.result
        if isinstance(result, AggregateResult):
            aggregates.append(result)
        elif isinstance(result, CountResult):
            counts.append(result)
        elif isinstance(result, InsightResult):
            for insight in result.entries:
                _dedup_insight(insights, insight)
        elif isinstance(result, RecallResult):
            for hit in result.hits:
                _offer(candidates, _hit_snapshot(hit), _relevance_from_distance(hit.distance))
        elif isinstance(result, TimelineResult):
            for snap in result.entries:
                _dedup_append(timeline_entries, snap)
                _offer(candidates, snap, _STRUCTURED_RELEVANCE)
        elif isinstance(result, LookupResult):
            for snap in result.entries:
                _offer(candidates, snap, _STRUCTURED_RELEVANCE)
        else:  # pragma: no cover — the union is closed; a new result type must be handled
            raise TypeError(f"unrankable retrieval result: {type(result).__name__}")

    # The same insight can arrive twice: as a payload-free snapshot through recall/timeline,
    # and as a full row through the insight family. 06's rule is that one memory is one
    # candidate; here the richer representation wins, because only it carries lineage.
    for insight in insights:
        candidates.pop(insight.id, None)

    ranked = _rank(list(candidates.values()))
    ranking = tuple(
        RankingEntry(
            memory_id=c.snapshot.id,
            relevance=round(c.relevance, 6),
            confidence=round(_confidence_axis(c.snapshot), 6),
            recency=round(_recency_axis(c.snapshot, ranked), 6),
            tier=round(_tier_axis(c.snapshot), 6),
            score=round(c._score, 6),
        )
        for c in ranked
    )

    included = _apply_budget(ranked, max_memories=max_memories, per_type_cap=per_type_cap)

    context = ContextBlock(
        question=question,
        aggregates=tuple(aggregates),
        counts=tuple(counts),
        memories=tuple(c.snapshot for c in included),
        omitted_count=len(ranked) - len(included),
        insights=tuple(insights),
    )
    trace = EvidenceTrace(
        trace_id=trace_id or uuid4(),
        question=question,
        retrieval_steps=tuple(steps),
        evidence=tuple(c.snapshot for c in ranked),  # complete: everything retrieved
        insights=tuple(
            InsightRef(
                id=i.id,
                hypothesis=i.hypothesis,
                evidence_ids=i.evidence_ids,
                pattern_strength=i.pattern_strength,
                retraction=(
                    render_retraction_condition(i.retraction_condition)
                    if i.retraction_condition
                    else None
                ),
            )
            for i in insights
        ),
        timeline=tuple(timeline_entries),
        ranking=ranking,
        assembled_at=assembled_at or datetime.now(timezone.utc),
        # Read off the context rather than recomputed here: one definition of "citable",
        # used by the narrator's prompt and the validator alike (ADR-14.8).
        citable_ids=context.citable_ids(),
    )
    return context, trace


# ── ranking internals (pure arithmetic over typed fields) ──────────────────────────────
def _rank(candidates: list[_Candidate]) -> list[_Candidate]:
    """Score every candidate on the four axes and return them best-first (score desc, then
    memory ID asc as a deterministic tiebreak). Recency is normalized across the candidate
    set, so it is computed once the set is known."""
    for c in candidates:
        c._score = (
            _W_RELEVANCE * c.relevance
            + _W_CONFIDENCE * _confidence_axis(c.snapshot)
            + _W_RECENCY * _recency_axis(c.snapshot, candidates)
            + _W_TIER * _tier_axis(c.snapshot)
        )
    return sorted(candidates, key=lambda c: (-c._score, str(c.snapshot.id)))


def _relevance_from_distance(distance: float) -> float:
    """Map L2 distance on unit vectors to a [0,1] relevance (nearer → higher)."""
    return max(0.0, min(1.0, 1.0 - distance / _MAX_L2))


def _confidence_axis(snap: EvidenceSnapshot) -> float:
    """Stated confidence, discounted by provenance so reconstructed memories rank below
    live ones of equal stated confidence. The recorded axis value is this effective number
    (what actually drove the score)."""
    factor = _PROVENANCE_FACTOR.get(snap.provenance, _PROVENANCE_DEFAULT)
    return max(0.0, min(1.0, snap.confidence * factor))


def _recency_axis(snap: EvidenceSnapshot, population: list[_Candidate]) -> float:
    """Temporal proximity **relative to the retrieved set**: newest candidate → 1.0,
    oldest → 0.0. Assembly has no 'now' and never parses the question's window, so recency
    is a within-set heuristic — deterministic and language-free. A degenerate set (one
    candidate, or all identical times) scores 1.0."""
    times = [c.snapshot.event_time for c in population]
    t_min, t_max = min(times), max(times)
    span = (t_max - t_min).total_seconds()
    if span <= 0:
        return 1.0
    return (snap.event_time - t_min).total_seconds() / span


def _tier_axis(snap: EvidenceSnapshot) -> float:
    """Tier-2 derived insights outrank tier-1 raw events (06). No insights exist before
    Phase 5, so today this is a constant for every candidate — wired now, exercised later."""
    return _TIER_SCORE.get(snap.type, _EPISODIC_TIER)


def _apply_budget(
    ranked: list[_Candidate], *, max_memories: int, per_type_cap: int
) -> list[_Candidate]:
    """Diversity cap then global budget, over the already-ranked list. The cap stops one
    memory type from filling the narration budget with near-identical rows (06); the budget
    bounds the raw-event context the model sees. Both preserve rank order."""
    per_type: dict[str, int] = {}
    kept: list[_Candidate] = []
    for c in ranked:
        if len(kept) >= max_memories:
            break
        seen = per_type.get(c.snapshot.type, 0)
        if seen >= per_type_cap:
            continue
        per_type[c.snapshot.type] = seen + 1
        kept.append(c)
    return kept


# ── small helpers ──────────────────────────────────────────────────────────────────────
def _offer(candidates: dict[UUID, _Candidate], snap: EvidenceSnapshot, relevance: float) -> None:
    """Register a memory as a candidate, or raise an existing candidate's relevance if this
    source matched it more strongly (dedup by ID — the Chicken-scenario merge: one memory
    surfaced by both structured lookup and recall becomes a single candidate)."""
    existing = candidates.get(snap.id)
    if existing is None:
        candidates[snap.id] = _Candidate(snapshot=snap, relevance=relevance)
    elif relevance > existing.relevance:
        existing.relevance = relevance


def _dedup_insight(entries: list[InsightRow], insight: InsightRow) -> None:
    if all(e.id != insight.id for e in entries):
        entries.append(insight)


def _dedup_append(entries: list[EvidenceSnapshot], snap: EvidenceSnapshot) -> None:
    if all(e.id != snap.id for e in entries):
        entries.append(snap)


def _hit_snapshot(hit) -> EvidenceSnapshot:
    """A RecallHit carries the same identity + display metadata as a snapshot, plus a
    distance; project it down (the distance lives on in the relevance axis)."""
    return EvidenceSnapshot(
        id=hit.id,
        type=hit.type,
        event_time=hit.event_time,
        confidence=hit.confidence,
        provenance=hit.provenance,
        summary=hit.summary,
    )
