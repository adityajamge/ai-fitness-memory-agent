"""Read-path query builders — the closed, parameterized families of 06-retrieval-strategy.

Boundary: engine/repository.py owns row-level queries serving the write path plus
single-row fetches; this module owns the builder families that answer questions and
return ``(result, RetrievalStep)`` pairs for evidence assembly (ADR-12). M1 ships the
aggregation family; recall / timeline / point-lookup follow (M2).

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

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import psycopg

from engine.trace import RetrievalStep


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
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise RetrievalSpecError("date_range bounds must be timezone-aware")
        if self.start >= self.end:
            raise RetrievalSpecError(
                f"empty date_range: start {self.start.isoformat()} >= end {self.end.isoformat()}"
            )


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
        else:
            out[key] = value
    return out
