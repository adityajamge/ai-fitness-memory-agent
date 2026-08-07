"""EvidenceTrace contracts (ADR-12) — the deterministic receipt of context assembly.

Design contract: 03-memory-engine.md §6. A trace is *emitted by assembly as a byproduct*,
never reconstructed after the fact and never an agent-callable tool; if context was
assembled, a trace exists, by construction. The ingestion ``Receipt``
(engine/ingestion.py) is the same artifact in miniature — one type, two renderings.

These are pure value objects: frozen, slotted, no I/O, no engine imports. ``to_json``
produces the JSONB-ready dict that Phase 6 (T7) persists into ``evidence_traces.trace``
verbatim and the glass-box UI renders. Snapshots carry memory IDs plus display metadata
only — **never payload copies** (the UI resolves a chip through the app API;
engine/schema.sql evidence_traces comment).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True, slots=True)
class RetrievalStep:
    """One executed query of an assembly — the glass box's "how this was retrieved" row.

    ``sql`` is the exact statement executed (placeholders included — builder families
    never interpolate values); ``params`` are the bound parameters, already display-ready
    (UUIDs/datetimes as strings) so the whole step is JSON-serializable as-is.
    ``row_count`` counts the memory rows that participated in the result (for an
    aggregation: contributing rows, not buckets).
    """

    family: str  # 'aggregate' | 'recall' | 'timeline' | 'lookup'
    sql: str
    params: Mapping[str, object]
    row_count: int

    def to_json(self) -> dict:
        return {
            "family": self.family,
            "sql": self.sql,
            "params": dict(self.params),
            "row_count": self.row_count,
        }


@dataclass(frozen=True, slots=True)
class EvidenceSnapshot:
    """A memory's identity + display metadata at assembly time. Deliberately payload-free."""

    id: UUID
    type: str
    event_time: datetime
    confidence: float
    provenance: str
    summary: str | None

    @classmethod
    def from_row(cls, row: Mapping) -> EvidenceSnapshot:
        """Build from a repository-shaped dict row (extra keys like ``payload`` ignored)."""
        return cls(
            id=row["id"],
            type=row["type"],
            event_time=row["event_time"],
            confidence=row["confidence"],
            provenance=row["provenance"],
            summary=row["summary"],
        )

    def to_json(self) -> dict:
        return {
            "id": str(self.id),
            "type": self.type,
            "event_time": self.event_time.isoformat(),
            "confidence": self.confidence,
            "provenance": self.provenance,
            "summary": self.summary,
        }


@dataclass(frozen=True, slots=True)
class InsightRef:
    """A derived insight that participated in an answer, with its lineage (evidence_ids).
    Populated from Phase 5 (T5/T6) onward; an empty ``insights`` tuple is honest, not an
    error."""

    id: UUID
    hypothesis: str
    evidence_ids: tuple[UUID, ...]

    def to_json(self) -> dict:
        return {
            "id": str(self.id),
            "hypothesis": self.hypothesis,
            "evidence_ids": [str(e) for e in self.evidence_ids],
        }


@dataclass(frozen=True, slots=True)
class RankingEntry:
    """Why a candidate memory was selected — the four committed scoring axes
    (06-retrieval-strategy.md: relevance, confidence, recency, tier) plus the composite.
    Weights are implementation detail (M3); the axes are architectural."""

    memory_id: UUID
    relevance: float
    confidence: float
    recency: float
    tier: float
    score: float

    def to_json(self) -> dict:
        return {
            "memory_id": str(self.memory_id),
            "relevance": self.relevance,
            "confidence": self.confidence,
            "recency": self.recency,
            "tier": self.tier,
            "score": self.score,
        }


@dataclass(frozen=True, slots=True)
class EvidenceTrace:
    """The full ADR-12 contract (03-memory-engine.md §6, field-for-field)."""

    trace_id: UUID
    question: str
    retrieval_steps: tuple[RetrievalStep, ...]
    evidence: tuple[EvidenceSnapshot, ...]
    insights: tuple[InsightRef, ...]
    timeline: tuple[EvidenceSnapshot, ...]
    ranking: tuple[RankingEntry, ...]
    assembled_at: datetime
    #: Every memory ID the narrator was permitted to cite on this turn — the *same* set as
    #: ``ContextBlock.citable_ids()``, carried here so the persisted trace is self-contained
    #: (ADR-14.8, resolved in glass-box-architecture.md §4.2).
    #:
    #: It is deliberately **not** derivable from ``evidence``. An aggregate's contributing
    #: memory IDs are citable but never appear as evidence snapshots — assembly is pure and
    #: does not hydrate them (ADR-14.7) — so a validator reading ``evidence`` alone would flag
    #: a *correct* citation of an aggregated meal as invalid. Serializing it sorted keeps the
    #: stored JSONB byte-stable for a given turn, which makes trace diffs meaningful.
    citable_ids: frozenset[UUID] = frozenset()

    def to_json(self) -> dict:
        return {
            "trace_id": str(self.trace_id),
            "question": self.question,
            "retrieval_steps": [s.to_json() for s in self.retrieval_steps],
            "evidence": [e.to_json() for e in self.evidence],
            "insights": [i.to_json() for i in self.insights],
            "timeline": [t.to_json() for t in self.timeline],
            "ranking": [r.to_json() for r in self.ranking],
            "assembled_at": self.assembled_at.isoformat(),
            "citable_ids": sorted(str(i) for i in self.citable_ids),
        }
