"""Read-path query builders — the closed, parameterized families of 06-retrieval-strategy.

Boundary: engine/repository.py owns row-level queries serving the write path plus
single-row fetches; this module owns the builder families that answer questions and
return ``(result, RetrievalStep)`` pairs for evidence assembly (ADR-12). The closed set:
**aggregate** (M1), **recall** (vector K-NN), **timeline** (ordered slice), **lookup**
(last/first event, item containment, type counts) (M2).

Security invariants (same as repository.py, ADR-13.4): every query filters on
``user_id``. SQL is composed only from module-constant fragments selected by validated
enum slots; every runtime value — the metric's JSONB path, the grouping period, the
timezone, the date range — is a **bound parameter**. No query string ever interpolates
user or model content; the agent can only pick a builder and fill typed slots
(03-memory-engine.md, "closed builder families").

Correctness notes:
- ``status='active'`` everywhere: a note superseded by reprocess_note (Phase 2) must
  never double-count against the typed events that replaced it.
- Metric values are numeric **by the write path's contract**: hot fields are validated/
  coerced by engine/types.py before insert, so ``::FLOAT8`` casts cannot fail on rows
  this engine wrote.
- Bucketing happens in the *question's* timezone (``AT TIME ZONE``): a 23:30 IST meal
  belongs to the user's day, not UTC's (12-test-plan.md "tz edges").
- Every aggregate carries its contributing memory IDs (deterministically ordered), so
  computed numbers stay citable in the glass box.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import psycopg
from psycopg.types.json import Jsonb

from engine.insights import CONSOLIDATION_SERIES
from engine.memory import vector_literal
from engine.model import ModelProvider
from engine.trace import EvidenceSnapshot, RetrievalStep
from engine.types import INSIGHT_KINDS, MEMORY_TYPE_REGISTRY


class RetrievalSpecError(ValueError):
    """A tool call's slots failed validation. Raised before any SQL is composed — the
    planner's mistake dies above the database (05-agent-architecture.md boundary)."""


# ── the metric whitelist (decision D-3) ────────────────────────────────────────────────
# One entry per aggregatable *typed hot field* of engine/types.py. The planner's `metric`
# slot must name a key here; the JSONB path is engine-owned and bound as a parameter.
# A new metric is one line — but always a deliberate, reviewed line (closed vocabulary).


@dataclass(frozen=True, slots=True)
class MetricDef:
    memory_type: str
    path: tuple[str, ...]  # JSONB path segments under `payload`


METRICS: dict[str, MetricDef] = {
    # meal → MealPayload.nutrition (Nutrition hot fields)
    "protein_g": MetricDef("meal", ("nutrition", "protein_g")),
    "carbs_g": MetricDef("meal", ("nutrition", "carbs_g")),
    "fat_g": MetricDef("meal", ("nutrition", "fat_g")),
    "kcal": MetricDef("meal", ("nutrition", "kcal")),
    # weight → WeightPayload
    "weight_kg": MetricDef("weight", ("weight_kg",)),
    # body_scan → BodyScanPayload (scans carry their own weight reading)
    "body_fat_pct": MetricDef("body_scan", ("body_fat_pct",)),
    "body_scan_weight_kg": MetricDef("body_scan", ("weight_kg",)),
    # sleep → SleepPayload
    "sleep_hours": MetricDef("sleep", ("hours",)),
    # workout → WorkoutPayload
    "workout_duration_min": MetricDef("workout", ("duration_min",)),
    "workout_distance_km": MetricDef("workout", ("distance_km",)),
    # supplement → SupplementPayload
    "supplement_dose_mg": MetricDef("supplement", ("dose_mg",)),
    # blood_report → BloodReportPayload.markers (Phase 5 §4.5). `markers` is an open
    # dict[str, float], so it has no typed hot field to derive a whitelist from — these five
    # are curated by hand, which is what keeps the planner's vocabulary closed (06). Marker
    # keys carry their unit, matching the qty_g / duration_min convention.
    "vitamin_d_ng_ml": MetricDef("blood_report", ("markers", "vitamin_d_ng_ml")),
    "vitamin_b12_pg_ml": MetricDef("blood_report", ("markers", "vitamin_b12_pg_ml")),
    "ferritin_ng_ml": MetricDef("blood_report", ("markers", "ferritin_ng_ml")),
    "ldl_mg_dl": MetricDef("blood_report", ("markers", "ldl_mg_dl")),
    "hba1c_pct": MetricDef("blood_report", ("markers", "hba1c_pct")),
}

_AGG_FNS = {"sum": "SUM", "avg": "AVG", "min": "MIN", "max": "MAX"}  # + 'count', special-cased
_AGGS = frozenset(_AGG_FNS) | {"count"}
_PERIODS = frozenset({"day", "week"})
_GROUPS = _PERIODS | {"none"}


@dataclass(frozen=True, slots=True)
class AggregateSpec:
    """Validated slots for one aggregation call. ``[start, end)`` is half-open, both
    aware; ``tz`` is the IANA zone buckets are computed in (normally the user's)."""

    metric: str
    start: datetime
    end: datetime
    tz: str
    agg: str = "sum"
    group_by: str = "none"

    def __post_init__(self) -> None:
        if self.metric not in METRICS:
            raise RetrievalSpecError(
                f"unknown metric {self.metric!r}; known: {sorted(METRICS)}"
            )
        if self.agg not in _AGGS:
            raise RetrievalSpecError(f"unknown agg {self.agg!r}; known: {sorted(_AGGS)}")
        if self.group_by not in _GROUPS:
            raise RetrievalSpecError(
                f"unknown group_by {self.group_by!r}; known: {sorted(_GROUPS)}"
            )
        try:
            ZoneInfo(self.tz)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise RetrievalSpecError(f"unknown timezone {self.tz!r}") from exc
        _validate_range(self.start, self.end)


@dataclass(frozen=True, slots=True)
class AggregateBucket:
    """One result bucket. ``bucket`` is the local ISO date the period starts on (None for
    an ungrouped total); ``evidence_ids`` are the contributing memory rows, in
    (event_time, id) order — the citation targets for this computed number."""

    bucket: str | None
    value: float
    n: int
    evidence_ids: tuple[UUID, ...]


@dataclass(frozen=True, slots=True)
class AggregateResult:
    """Zero matching rows yields ``buckets == ()`` for every grouping — the *defined*
    empty result the narrator must render honestly ("no logged data in that window")."""

    spec: AggregateSpec
    buckets: tuple[AggregateBucket, ...]

    @property
    def is_empty(self) -> bool:
        return not self.buckets


# SQL fragments (vetted, module-constant — the only material queries are composed from).
# `{value}` is filled from _AGG_FNS/COUNT below: builder-owned SQL, never runtime data.
# GROUP BY 1 (ordinal) sidesteps alias-resolution dialect trivia.
_WHERE = """
FROM memories
WHERE user_id = %(user_id)s
  AND type = %(type)s
  AND status = 'active'
  AND event_time >= %(start)s
  AND event_time < %(end)s
  AND payload #>> %(path)s::TEXT[] IS NOT NULL
"""

_SQL_GROUPED = (
    """
SELECT date_trunc(%(period)s, event_time AT TIME ZONE %(tz)s) AS bucket,
       {value} AS value,
       COUNT(*) AS n,
       array_agg(id ORDER BY event_time, id) AS evidence_ids
"""
    + _WHERE
    + "GROUP BY 1\nORDER BY 1"
)

_SQL_TOTAL = (
    """
SELECT {value} AS value,
       COUNT(*) AS n,
       array_agg(id ORDER BY event_time, id) AS evidence_ids
"""
    + _WHERE
)


def aggregate_memories(
    cur: psycopg.Cursor, user_id: UUID, spec: AggregateSpec
) -> tuple[AggregateResult, RetrievalStep]:
    """Execute one aggregation over typed payloads; return the result and the executed
    query as a RetrievalStep (the trace's "how this was retrieved" row, ADR-12).

    Semantics: aggregates run over rows *where the metric is present* — ``count`` counts
    logged values of the metric, not rows of the type. (Type-level event counting is the
    M2 lookup family's job.)
    """
    metric = METRICS[spec.metric]
    value_expr = (
        "COUNT(*)::FLOAT8"
        if spec.agg == "count"
        else f"{_AGG_FNS[spec.agg]}((payload #>> %(path)s::TEXT[])::FLOAT8)"
    )

    grouped = spec.group_by != "none"
    sql = (_SQL_GROUPED if grouped else _SQL_TOTAL).format(value=value_expr).strip()
    params: dict[str, object] = {
        "user_id": user_id,
        "type": metric.memory_type,
        "start": spec.start,
        "end": spec.end,
        "path": list(metric.path),
    }
    if grouped:
        params["period"] = spec.group_by
        params["tz"] = spec.tz

    cur.execute(sql, params)
    rows = cur.fetchall()

    buckets = tuple(
        AggregateBucket(
            bucket=row["bucket"].date().isoformat() if grouped else None,
            value=float(row["value"]),
            n=int(row["n"]),
            evidence_ids=tuple(row["evidence_ids"]),
        )
        for row in rows
        if row["n"]  # the ungrouped query returns one all-NULL row when nothing matched
    )

    result = AggregateResult(spec=spec, buckets=buckets)
    step = RetrievalStep(
        family="aggregate",
        sql=sql,
        params=_display_params(params),
        row_count=sum(b.n for b in buckets),
    )
    return result, step


def _display_params(params: dict[str, object]) -> dict[str, object]:
    """Bound parameters, made JSON-ready for the trace (RetrievalStep contract)."""
    out: dict[str, object] = {}
    for key, value in params.items():
        if isinstance(value, UUID):
            out[key] = str(value)
        elif isinstance(value, datetime):
            out[key] = value.isoformat()
        elif isinstance(value, Jsonb):
            out[key] = value.obj
        else:
            out[key] = value
    return out


def _validate_range(start: datetime, end: datetime) -> None:
    if start.tzinfo is None or end.tzinfo is None:
        raise RetrievalSpecError("date_range bounds must be timezone-aware")
    if start >= end:
        raise RetrievalSpecError(
            f"empty date_range: start {start.isoformat()} >= end {end.isoformat()}"
        )


def _validate_type(memory_type: str) -> None:
    if memory_type not in MEMORY_TYPE_REGISTRY:
        raise RetrievalSpecError(
            f"unknown memory type {memory_type!r}; known: {sorted(MEMORY_TYPE_REGISTRY)}"
        )


# ── recall: vector K-NN over summary embeddings (06 "narrative / semantic" path) ───────

_DIMS = 512  # VECTOR(512) — ADR-13.2; must match engine/schema.sql and vector_literal
_MAX_TOP_K = 50
# What the trace shows instead of 512 floats. The query *text* is in the params; the
# vector is model output, huge, and meaningless to display (ADR-12 traces reference,
# never bulk-copy).
_QVEC_DISPLAY = f"<{_DIMS}-d unit vector of 'query'>"


def embed_query(model: ModelProvider, text: str) -> list[float]:
    """Embed a recall query through the same pipeline as stored summaries (normalized
    512-dim — distances are comparable by construction). Call this OUTSIDE any
    transaction (engine/db.py discipline: model calls never ride a connection); pass the
    result to recall_memories. Raises EmbeddingError on failure — before any SQL runs."""
    return model.embed([text])[0]


@dataclass(frozen=True, slots=True)
class RecallSpec:
    """Validated slots for one semantic recall. ``start``/``end`` are both-or-neither;
    ``type`` narrows to one registry type. The query text rides into the trace; the
    engine (not the agent) turns it into a vector — decision D-4."""

    query: str
    top_k: int = 8
    type: str | None = None
    start: datetime | None = None
    end: datetime | None = None

    def __post_init__(self) -> None:
        if not self.query.strip():
            raise RetrievalSpecError("recall query must not be empty")
        if not 1 <= self.top_k <= _MAX_TOP_K:
            raise RetrievalSpecError(f"top_k must be in 1..{_MAX_TOP_K}, got {self.top_k}")
        if self.type is not None:
            _validate_type(self.type)
        if (self.start is None) != (self.end is None):
            raise RetrievalSpecError("date_range needs both start and end (or neither)")
        if self.start is not None and self.end is not None:
            _validate_range(self.start, self.end)


@dataclass(frozen=True, slots=True)
class RecallHit:
    """One K-NN hit: snapshot metadata + L2 distance (≡ cosine ranking on unit vectors)."""

    id: UUID
    type: str
    event_time: datetime
    confidence: float
    provenance: str
    summary: str | None
    distance: float


@dataclass(frozen=True, slots=True)
class RecallResult:
    spec: RecallSpec
    hits: tuple[RecallHit, ...]

    @property
    def is_empty(self) -> bool:
        return not self.hits


# The ORDER BY repeats the distance expression (not the alias) — the exact shape the T1
# canary proved drives the C-SPANN index. NULL embeddings are excluded explicitly:
# unembedded rows (T15 backfill pending) are invisible to recall, never errors.
_SQL_RECALL_HEAD = """
SELECT id, type, event_time, confidence, provenance, summary,
       embedding <-> %(qvec)s::VECTOR(512) AS distance
FROM memories
WHERE user_id = %(user_id)s
  AND status = 'active'
  AND embedding IS NOT NULL
"""
_FRAG_TYPE = "  AND type = %(type)s\n"
_FRAG_RANGE = "  AND event_time >= %(start)s\n  AND event_time < %(end)s\n"
_SQL_RECALL_TAIL = "ORDER BY embedding <-> %(qvec)s::VECTOR(512)\nLIMIT %(top_k)s"


def recall_memories(
    cur: psycopg.Cursor, user_id: UUID, spec: RecallSpec, query_vec: list[float]
) -> tuple[RecallResult, RetrievalStep]:
    """Execute one vector K-NN over summary embeddings. ``query_vec`` comes from
    embed_query (computed outside the transaction); the builder itself is pure SQL."""
    if len(query_vec) != _DIMS:
        raise RetrievalSpecError(f"query vector must be {_DIMS}-dim, got {len(query_vec)}")

    sql = _SQL_RECALL_HEAD
    params: dict[str, object] = {
        "qvec": vector_literal(query_vec),
        "user_id": user_id,
        "top_k": spec.top_k,
    }
    if spec.type is not None:
        sql += _FRAG_TYPE
        params["type"] = spec.type
    if spec.start is not None and spec.end is not None:
        sql += _FRAG_RANGE
        params["start"] = spec.start
        params["end"] = spec.end
    sql = (sql + _SQL_RECALL_TAIL).strip()

    cur.execute(sql, params)
    hits = tuple(
        RecallHit(
            id=row["id"],
            type=row["type"],
            event_time=row["event_time"],
            confidence=row["confidence"],
            provenance=row["provenance"],
            summary=row["summary"],
            distance=float(row["distance"]),
        )
        for row in cur.fetchall()
    )

    display = _display_params({**params, "qvec": _QVEC_DISPLAY, "query": spec.query})
    step = RetrievalStep(family="recall", sql=sql, params=display, row_count=len(hits))
    return RecallResult(spec=spec, hits=hits), step


# ── timeline: ordered typed slice (06 point-lookup/timeline path, UI timeline strip) ──

_MAX_TIMELINE = 1000


@dataclass(frozen=True, slots=True)
class TimelineSpec:
    start: datetime
    end: datetime
    types: tuple[str, ...] | None = None
    limit: int = 200

    def __post_init__(self) -> None:
        _validate_range(self.start, self.end)
        if self.types is not None:
            if not self.types:
                raise RetrievalSpecError("types filter must not be empty (use None for all)")
            for t in self.types:
                _validate_type(t)
        if not 1 <= self.limit <= _MAX_TIMELINE:
            raise RetrievalSpecError(f"limit must be in 1..{_MAX_TIMELINE}, got {self.limit}")


@dataclass(frozen=True, slots=True)
class TimelineResult:
    """Entries are payload-free EvidenceSnapshots — exactly what EvidenceTrace.timeline
    carries, so assembly (M3) can pass them through unchanged."""

    spec: TimelineSpec
    entries: tuple[EvidenceSnapshot, ...]

    @property
    def is_empty(self) -> bool:
        return not self.entries


_SQL_TIMELINE_HEAD = """
SELECT id, type, event_time, confidence, provenance, summary
FROM memories
WHERE user_id = %(user_id)s
  AND status = 'active'
  AND event_time >= %(start)s
  AND event_time < %(end)s
"""
_FRAG_TYPES = "  AND type = ANY(%(types)s)\n"
_SQL_TIMELINE_TAIL = "ORDER BY event_time, id\nLIMIT %(limit)s"


def get_timeline(
    cur: psycopg.Cursor, user_id: UUID, spec: TimelineSpec
) -> tuple[TimelineResult, RetrievalStep]:
    """Ordered event slice for a date range (event_time ascending, id tiebreak)."""
    sql = _SQL_TIMELINE_HEAD
    params: dict[str, object] = {
        "user_id": user_id,
        "start": spec.start,
        "end": spec.end,
        "limit": spec.limit,
    }
    if spec.types is not None:
        sql += _FRAG_TYPES
        params["types"] = list(spec.types)
    sql = (sql + _SQL_TIMELINE_TAIL).strip()

    cur.execute(sql, params)
    entries = tuple(EvidenceSnapshot.from_row(row) for row in cur.fetchall())

    step = RetrievalStep(
        family="timeline", sql=sql, params=_display_params(params), row_count=len(entries)
    )
    return TimelineResult(spec=spec, entries=entries), step


# ── lookup: last/first event, item containment, type counts ───────────────────────────

_MAX_LOOKUP = 20
_DIRECTIONS = {"last": "DESC", "first": "ASC"}  # validated enum → module constant


@dataclass(frozen=True, slots=True)
class LookupSpec:
    """Point lookup: the newest/oldest event(s) of a type, optionally matching an item
    by exact containment over the extracted payload (inverted index — the structured
    path for "when did I last eat chicken?"; vector recall is the fuzzy fallback, and
    the trace records which ran — 06)."""

    type: str
    direction: str = "last"
    item: str | None = None
    n: int = 1

    def __post_init__(self) -> None:
        _validate_type(self.type)
        if self.direction not in _DIRECTIONS:
            raise RetrievalSpecError(
                f"direction must be one of {sorted(_DIRECTIONS)}, got {self.direction!r}"
            )
        if self.item is not None and not self.item.strip():
            raise RetrievalSpecError("item filter must not be empty (use None for no filter)")
        if not 1 <= self.n <= _MAX_LOOKUP:
            raise RetrievalSpecError(f"n must be in 1..{_MAX_LOOKUP}, got {self.n}")


@dataclass(frozen=True, slots=True)
class LookupResult:
    spec: LookupSpec
    entries: tuple[EvidenceSnapshot, ...]

    @property
    def is_empty(self) -> bool:
        return not self.entries


_SQL_LOOKUP_HEAD = """
SELECT id, type, event_time, confidence, provenance, summary
FROM memories
WHERE user_id = %(user_id)s
  AND status = 'active'
  AND type = %(type)s
"""
# The item variant additionally selects payload: the match runs in Python (see
# normalize_item below), so the compared value must travel with the row.
_SQL_LOOKUP_HEAD_ITEM = """
SELECT id, type, event_time, confidence, provenance, summary, payload
FROM memories
WHERE user_id = %(user_id)s
  AND status = 'active'
  AND type = %(type)s
"""
# {order} is filled from _DIRECTIONS only — builder-owned SQL, never runtime data.
_SQL_LOOKUP_TAIL = "ORDER BY event_time {order}, id {order}\nLIMIT %(n)s"
# No LIMIT here: it would apply before the item match runs, discarding the wrong rows.
# The comment is part of the executed statement (legal SQL), so `sql` on the trace step
# stays literally true to what ran — the item filter and LIMIT both apply in-engine,
# post-fetch, via normalize_item() (§4.13 — this is a query-time stop-gap, not permanent
# engine behavior; superseded by Phase 5 canonicalization).
_SQL_LOOKUP_TAIL_ITEM = (
    "-- item filter + LIMIT applied in-engine via normalize_item() (§4.13)\n"
    "ORDER BY event_time {order}, id {order}"
)

_ITEM_STRIP_CATEGORY = "P"  # Unicode general category prefix for punctuation
_WHITESPACE_RUN_RE = re.compile(r"\s+")


def _is_item_edge_char(ch: str) -> bool:
    return ch.isspace() or unicodedata.category(ch).startswith(_ITEM_STRIP_CATEGORY)


def normalize_item(s: str) -> str:
    """Query-time item-name normalization (§4.13 stop-gap: "normalize how a token is
    *written*, not what it *means*"). Deterministic, pure, and idempotent — must be applied
    identically to the search term and the stored value; asymmetric application is a
    matching bug by construction. Lives entirely here so it is deletable in one commit when
    Phase 5's canonicalization engine lands; do not extend beyond these six steps (the
    explicit non-goal list in docs/engineering/replay-architecture.md §4.13 is a contract,
    not a backlog).

    Raises ``RetrievalSpecError`` if the term normalizes to empty rather than matching
    everything (ADR-14.11 strict slots).
    """
    s = unicodedata.normalize("NFC", s)
    s = s.casefold()
    # Not redundant: case folding is not closed under normalization (UAX #15), so a second
    # NFC pass is required for the idempotence guarantee below to hold.
    s = unicodedata.normalize("NFC", s)

    start, end = 0, len(s)
    while start < end and _is_item_edge_char(s[start]):
        start += 1
    while end > start and _is_item_edge_char(s[end - 1]):
        end -= 1
    s = s[start:end]

    s = _WHITESPACE_RUN_RE.sub(" ", s)

    if not s:
        raise RetrievalSpecError("item term normalizes to empty")
    return s


def _item_names(payload: dict) -> list[str]:
    """Extracted item names a lookup can match against. Only meal-shaped payloads carry an
    ``items`` array (engine/types.py MealPayload); other types yield none, exactly as the
    prior JSONB-containment filter also matched only meal payloads."""
    return [item["name"] for item in payload.get("items", []) if item.get("name")]


def lookup_events(
    cur: psycopg.Cursor, user_id: UUID, spec: LookupSpec
) -> tuple[LookupResult, RetrievalStep]:
    """Newest/oldest event(s) of a type, optionally filtered by item match.

    Item matching (§4.13) is exact after ``normalize_item()`` — case, edge punctuation, and
    whitespace insensitive, but never fuzzy (no stemming, substring, or synonym matching).
    It runs in Python: CockroachDB has neither Unicode NFC normalization nor full casefold,
    so any SQL-side approximation would be a *different* function on the two sides, breaking
    the symmetry the match depends on.
    """
    order = _DIRECTIONS[spec.direction]

    if spec.item is None:
        sql = (_SQL_LOOKUP_HEAD + _SQL_LOOKUP_TAIL.format(order=order)).strip()
        params: dict[str, object] = {"user_id": user_id, "type": spec.type, "n": spec.n}
        cur.execute(sql, params)
        entries = tuple(EvidenceSnapshot.from_row(row) for row in cur.fetchall())
        step = RetrievalStep(
            family="lookup", sql=sql, params=_display_params(params), row_count=len(entries)
        )
        return LookupResult(spec=spec, entries=entries), step

    normalized_term = normalize_item(spec.item)
    sql = (_SQL_LOOKUP_HEAD_ITEM + _SQL_LOOKUP_TAIL_ITEM.format(order=order)).strip()
    params = {"user_id": user_id, "type": spec.type}
    cur.execute(sql, params)

    matched = []
    for row in cur.fetchall():
        if any(normalize_item(name) == normalized_term for name in _item_names(row["payload"])):
            matched.append(row)
            if len(matched) >= spec.n:
                break
    entries = tuple(EvidenceSnapshot.from_row(row) for row in matched)

    display = _display_params(params)
    display["item"] = spec.item
    display["item_normalized"] = normalized_term
    display["n"] = spec.n
    step = RetrievalStep(family="lookup", sql=sql, params=display, row_count=len(entries))
    return LookupResult(spec=spec, entries=entries), step


# ── insight lookup: the sixth family (06 "insight reuse", Phase 5 §4.10) ──────────────
#
# **Read-only, like every other builder** (I-17). "Has this been flagged before?" is a
# question, not a request to think: deriving a *new* insight is `analyze_series`, which the
# graph dispatches outside this transaction precisely because it writes. Nothing in this
# family inserts, updates, or triggers consolidation.
#
# Why this family exists at all, rather than letting insights arrive as EvidenceSnapshots
# through recall/timeline: a snapshot is **deliberately payload-free** (ADR-12 — a trace
# references memories, it never copies payloads), and an insight's lineage lives *in* its
# payload. Retrieving insights through the snapshot-shaped families would either lose the
# lineage or force a payload copy into the snapshot. A dedicated result type resolves it with
# nothing bent: `InsightRef` is a distinct trace type that already carries lineage (§4.10).

_MAX_INSIGHTS = 20
_INSIGHT_STATUSES = frozenset({"active", "retracted", "superseded"})


@dataclass(frozen=True, slots=True)
class InsightSpec:
    """Validated slots for one insight lookup.

    ``status`` defaults to ``active``, matching every other builder — a retracted claim must
    not reappear in an answer by accident. It is settable because the glass box is *supposed*
    to be able to show what the engine used to think (ADR-9), and that has to be a deliberate
    request rather than a default.
    """

    metric: str | None = None
    kind: str | None = None
    status: str = "active"
    limit: int = 10

    def __post_init__(self) -> None:
        if self.metric is not None and self.metric not in CONSOLIDATION_SERIES:
            raise RetrievalSpecError(
                f"unknown insight series {self.metric!r}; known: {sorted(CONSOLIDATION_SERIES)}"
            )
        if self.kind is not None and self.kind not in INSIGHT_KINDS:
            raise RetrievalSpecError(
                f"unknown insight kind {self.kind!r}; known: {sorted(INSIGHT_KINDS)}"
            )
        if self.status not in _INSIGHT_STATUSES:
            raise RetrievalSpecError(
                f"unknown status {self.status!r}; known: {sorted(_INSIGHT_STATUSES)}"
            )
        if not 1 <= self.limit <= _MAX_INSIGHTS:
            raise RetrievalSpecError(f"limit must be in 1..{_MAX_INSIGHTS}, got {self.limit}")


@dataclass(frozen=True, slots=True)
class InsightRow:
    """One derived insight, with the lineage a snapshot cannot carry.

    ``evidence_ids`` is the boundary-anchored, capped lineage (§4.2) and ``evidence_count`` the
    true total — rendering the first without the second would quietly under-report the evidence.
    """

    id: UUID
    kind: str
    series_metric: str
    hypothesis: str
    evidence_ids: tuple[UUID, ...]
    evidence_count: int
    pattern_strength: float
    effect: float
    coverage: float
    specificity: float
    window_start: datetime
    window_end: datetime
    event_time: datetime
    confidence: float
    provenance: str
    status: str


@dataclass(frozen=True, slots=True)
class InsightResult:
    spec: InsightSpec
    entries: tuple[InsightRow, ...]

    @property
    def is_empty(self) -> bool:
        return not self.entries


_SQL_INSIGHTS_HEAD = """
SELECT id, event_time, confidence, provenance, status, payload
FROM memories
WHERE user_id = %(user_id)s
  AND type = 'insight'
  AND status = %(status)s
"""
_FRAG_INSIGHT_METRIC = "  AND payload ->> 'series_metric' = %(metric)s\n"
_FRAG_INSIGHT_KIND = "  AND payload ->> 'kind' = %(kind)s\n"
_SQL_INSIGHTS_TAIL = "ORDER BY event_time DESC, id\nLIMIT %(limit)s"


def lookup_insights(
    cur: psycopg.Cursor, user_id: UUID, spec: InsightSpec
) -> tuple[InsightResult, RetrievalStep]:
    """Derived insights for a user, newest first — the structured half of 06's insight reuse.

    Freshness is deliberately **not** checked here. Deciding that a claim is stale and
    recomputing it is a write, and a read-only builder must not do it (I-17); that is
    ``analyze_series``'s job, dispatched by the graph outside this transaction.
    """
    sql = _SQL_INSIGHTS_HEAD
    params: dict[str, object] = {
        "user_id": user_id,
        "status": spec.status,
        "limit": spec.limit,
    }
    if spec.metric is not None:
        sql += _FRAG_INSIGHT_METRIC
        params["metric"] = spec.metric
    if spec.kind is not None:
        sql += _FRAG_INSIGHT_KIND
        params["kind"] = spec.kind
    sql = (sql + _SQL_INSIGHTS_TAIL).strip()

    cur.execute(sql, params)
    entries = tuple(_insight_row(row) for row in cur.fetchall())

    step = RetrievalStep(
        family="insight", sql=sql, params=_display_params(params), row_count=len(entries)
    )
    return InsightResult(spec=spec, entries=entries), step


def _insight_row(row: dict) -> InsightRow:
    """Project a stored insight into its typed read model.

    Tolerant of missing optional keys so a row written by an older Phase 5 milestone still
    reads — the payload registry's ``extra="allow"`` posture applied to the read side."""
    payload = row["payload"]
    return InsightRow(
        id=row["id"],
        kind=payload.get("kind", ""),
        series_metric=payload.get("series_metric", ""),
        hypothesis=payload.get("hypothesis", ""),
        evidence_ids=tuple(UUID(i) for i in payload.get("evidence_ids", [])),
        evidence_count=int(payload.get("evidence_count", 0)),
        pattern_strength=float(payload.get("pattern_strength", 0.0)),
        effect=float(payload.get("effect", 0.0)),
        coverage=float(payload.get("coverage", 0.0)),
        specificity=float(payload.get("specificity", 0.0)),
        window_start=_parse_moment(payload.get("window_start")) or row["event_time"],
        window_end=_parse_moment(payload.get("window_end")) or row["event_time"],
        event_time=row["event_time"],
        confidence=row["confidence"],
        provenance=row["provenance"],
        status=row["status"],
    )


def _parse_moment(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:  # pragma: no cover — payloads are written by payload_to_json
            return None
    return None


@dataclass(frozen=True, slots=True)
class CountSpec:
    """Type-level event count over a range — "how many workouts in June?". Counts rows
    of the type regardless of which metrics they carry (the aggregate family's ``count``
    counts logged *values* of one metric; this counts *events*)."""

    type: str
    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        _validate_type(self.type)
        _validate_range(self.start, self.end)


@dataclass(frozen=True, slots=True)
class CountResult:
    spec: CountSpec
    n: int
    evidence_ids: tuple[UUID, ...]


_SQL_COUNT = """
SELECT COUNT(*) AS n,
       array_agg(id ORDER BY event_time, id) AS evidence_ids
FROM memories
WHERE user_id = %(user_id)s
  AND type = %(type)s
  AND status = 'active'
  AND event_time >= %(start)s
  AND event_time < %(end)s
"""


def count_events(
    cur: psycopg.Cursor, user_id: UUID, spec: CountSpec
) -> tuple[CountResult, RetrievalStep]:
    """Count events of a type in a range, with the contributing IDs (citable counts)."""
    sql = _SQL_COUNT.strip()
    params: dict[str, object] = {
        "user_id": user_id,
        "type": spec.type,
        "start": spec.start,
        "end": spec.end,
    }
    cur.execute(sql, params)
    row = cur.fetchone()
    n = int(row["n"])
    ids = tuple(row["evidence_ids"]) if row["evidence_ids"] else ()

    step = RetrievalStep(family="lookup", sql=sql, params=_display_params(params), row_count=n)
    return CountResult(spec=spec, n=n, evidence_ids=ids), step
