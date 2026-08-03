"""Insight vocabulary + claim identity (Phase 5 M1).

Locked architecture: ``docs/engineering/consolidation-architecture.md`` — §4.4 (what a series
is), §4.6 (identity by fingerprint), §4.13 (the strength factors), §4.14 (retraction
conditions and their prose). The payload *models* live in
``engine/types.py``; this module owns the closed vocabularies around them and the two pure
functions that keep claims identifiable and explicable.

**Pure, like ``engine/trace.py``:** no I/O, no database, no clock, no model, no global state.
Everything here is a function of its arguments, which is what lets M2's detectors and M3's
identity comparison be fixture-tested without a cluster.

**The one-way dependency, stated because it is deliberate.** This module imports
``engine.types`` and *nothing else from the engine* — in particular **not**
``engine.retrieval``, even though ``CONSOLIDATION_SERIES`` is defined as a subset of that
module's ``METRICS`` (**I-9**). Importing it would invert the layering the moment M5 adds the
insight builder family, since ``retrieval`` will then need to read these contracts: the cycle
would be created by this line and paid for two milestones later. The subset relationship is
therefore enforced by a test that imports both (``engine/tests/test_insight_types.py``),
which is the same posture the payload registry's drift canary already takes — the guarantee
lives in a test that fails loudly, not in an import that constrains the architecture.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from typing import Literal

from engine.types import (
    INSIGHT_KINDS,
    MAX_EVIDENCE_IDS,
    SERIES_KINDS,
    RetractionCondition,
)

__all__ = [
    "SeriesDef",
    "SeriesKey",
    "CONSOLIDATION_SERIES",
    "INSIGHT_KINDS",
    "SERIES_KINDS",
    "MAX_EVIDENCE_IDS",
    "UnknownSeries",
    "validate_series",
    "fingerprint",
    "render_retraction_condition",
    "FINGERPRINT_VERSION",
    "FINGERPRINT_PRECISION",
]


class UnknownSeries(ValueError):
    """A metric outside ``CONSOLIDATION_SERIES`` was offered to the insight layer. Raised
    before anything is computed or written — the same strict-slots posture as
    ``RetrievalSpecError`` (ADR-14.11): nothing is defaulted into existence."""


@dataclass(frozen=True, slots=True)
class SeriesDef:
    """One consolidatable series: what it is about, which detector may run on it, and how to
    say its name and unit in prose.

    ``label``/``unit`` exist for ``render_retraction_condition`` and the Phase 6 UI. They are
    display strings only — nothing deterministic ever branches on them.
    """

    kind: Literal["behavioural", "outcome"]
    detector: Literal["level_shift", "intervention_outcome"]
    label: str
    unit: str


#: The closed vocabulary of series consolidation may run on (**I-9**) — a strict *subset* of
#: ``engine.retrieval.METRICS``, not a copy of it. The narrowing is load-bearing for the time
#: budget: a meal payload carries protein, carbs, fat and kcal, but only ``protein_g`` is
#: consolidatable, so logging a meal triggers **one** series scan rather than four (§4.4, §4.8).
#: Adding a line here is a deliberate, reviewed act.
#:
#: Which detector each series gets follows from how it is measured, not from preference
#: (§4.1): behaviours are logged densely enough to have a *level*, outcomes are measured
#: sparsely and only support a before/after statement.
CONSOLIDATION_SERIES: dict[str, SeriesDef] = {
    # ── behavioural: things the user does, logged densely ─────────────────────────────
    "protein_g": SeriesDef("behavioural", "level_shift", "protein", "g/day"),
    "sleep_hours": SeriesDef("behavioural", "level_shift", "sleep", "h/night"),
    # ── outcome: things the user measures, sparsely ───────────────────────────────────
    "body_fat_pct": SeriesDef("outcome", "intervention_outcome", "body fat", "%"),
    "weight_kg": SeriesDef("outcome", "intervention_outcome", "weight", "kg"),
    # blood markers (§4.5) — curated by hand so the vocabulary stays closed even though
    # BloodReportPayload.markers is an open dict. Units are baked into the key, matching the
    # qty_g / duration_min convention the extraction prompt already teaches.
    "vitamin_d_ng_ml": SeriesDef("outcome", "intervention_outcome", "vitamin D", "ng/mL"),
    "vitamin_b12_pg_ml": SeriesDef("outcome", "intervention_outcome", "vitamin B12", "pg/mL"),
    "ferritin_ng_ml": SeriesDef("outcome", "intervention_outcome", "ferritin", "ng/mL"),
    "ldl_mg_dl": SeriesDef("outcome", "intervention_outcome", "LDL cholesterol", "mg/dL"),
    "hba1c_pct": SeriesDef("outcome", "intervention_outcome", "HbA1c", "%"),
}


@dataclass(frozen=True, slots=True)
class SeriesKey:
    """What an insight is *about* — ``(kind, metric)`` (§4.4).

    Half of the identity triple ``(user_id, kind, series_key)`` that keeps one active insight
    per claim (§4.6, **I-10**), so it must be hashable, comparable, and stable in its string
    form: M3 compares these, and a rendering change would silently orphan every existing
    insight into a duplicate.
    """

    kind: str
    metric: str

    def __post_init__(self) -> None:
        if self.metric not in CONSOLIDATION_SERIES:
            raise UnknownSeries(
                f"{self.metric!r} is not consolidatable; known: {sorted(CONSOLIDATION_SERIES)}"
            )
        expected = CONSOLIDATION_SERIES[self.metric].kind
        if self.kind != expected:
            raise UnknownSeries(
                f"series {self.metric!r} is {expected!r}, not {self.kind!r}"
            )

    @classmethod
    def for_metric(cls, metric: str) -> SeriesKey:
        """The only constructor callers should need — the kind follows from the metric, so
        offering it as a free parameter would invite the two to disagree."""
        definition = CONSOLIDATION_SERIES.get(metric)
        if definition is None:
            raise UnknownSeries(
                f"{metric!r} is not consolidatable; known: {sorted(CONSOLIDATION_SERIES)}"
            )
        return cls(kind=definition.kind, metric=metric)

    @property
    def definition(self) -> SeriesDef:
        return CONSOLIDATION_SERIES[self.metric]

    @property
    def detector(self) -> str:
        return self.definition.detector

    def __str__(self) -> str:
        return f"{self.kind}:{self.metric}"


def validate_series(metric: str, kind: str | None = None) -> SeriesKey:
    """Resolve a metric to its ``SeriesKey``, raising ``UnknownSeries`` if it is not
    consolidatable (or if a supplied ``kind`` contradicts the registry).

    This is where ``InsightPayload.series_metric`` membership is checked — deliberately not in
    the Pydantic model, see the module docstring's note on the one-way dependency.
    """
    key = SeriesKey.for_metric(metric)
    if kind is not None and kind != key.kind:
        raise UnknownSeries(f"series {metric!r} is {key.kind!r}, not {kind!r}")
    return key


# ── claim identity (§4.6) ──────────────────────────────────────────────────────────────
#: Bumping this invalidates every stored fingerprint at once, which is the intended escape
#: hatch if the canonical form ever has to change: every claim reports as drifted and is
#: superseded through the normal path, rather than silently comparing across two formats.
FINGERPRINT_VERSION = "1"

#: Decimal places values are rounded to before hashing. Coarse enough that float noise in a
#: recomputation does not read as a changed claim; fine enough that a real change does.
FINGERPRINT_PRECISION = 3


def _canonical_value(value: float) -> str:
    # `+ 0.0` normalizes -0.0 to 0.0, so a sign that carries no meaning cannot change identity.
    return f"{round(value, FINGERPRINT_PRECISION) + 0.0:.{FINGERPRINT_PRECISION}f}"


def _canonical_time(moment: datetime) -> str:
    """UTC, second resolution. Normalizing the zone means the same instant expressed in
    ``+05:30`` and in ``Z`` is one claim, not two."""
    if moment.tzinfo is None:
        raise ValueError("fingerprint timestamps must be timezone-aware")
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fingerprint(
    *,
    kind: str,
    series_metric: str,
    window_start: datetime,
    window_end: datetime,
    values: Sequence[float],
    intervention_ids: Iterable[str] = (),
) -> str:
    """The deterministic identity of a *claim* (§4.6).

    Over the claim's defining content only: what kind of claim it is, which series, the window
    it spans, the values that make it, and which interventions it attributes to. **Not** over
    the hypothesis prose — rewording a sentence must not supersede a claim that has not
    changed, which is the same failure mode content-keyed ``record_id``s had in Phase 4
    (replay-architecture §4.3), one layer up.

    Interventions are sorted, so the order a detector happened to find them in is not part of
    the claim's identity.
    """
    if kind not in INSIGHT_KINDS:
        raise ValueError(f"unknown insight kind {kind!r}; known: {sorted(INSIGHT_KINDS)}")
    parts = [
        FINGERPRINT_VERSION,
        kind,
        series_metric,
        _canonical_time(window_start),
        _canonical_time(window_end),
        ",".join(_canonical_value(v) for v in values),
        ",".join(sorted(str(i) for i in intervention_ids)),
    ]
    return sha256("|".join(parts).encode("utf-8")).hexdigest()


# ── retraction prose (§4.14) ───────────────────────────────────────────────────────────
_DIRECTION_WITH_THRESHOLD = {"falling": "drops below", "rising": "rises above"}
_DIRECTION_RELATIVE = {
    "falling": "falls back below the level this is based on",
    "rising": "rises back above the level this is based on",
}


def render_retraction_condition(condition: RetractionCondition) -> str:
    """Render a typed condition as the sentence the UI shows (ADR-13.11: "prose is rendered
    from the object").

    Deterministic and pure: the same condition always renders the same sentence, and the
    sentence is never stored — regenerating it from the object is what guarantees the rule a
    user reads is the rule the evaluator runs. A stored sentence can drift from its condition;
    a rendered one cannot.

    Unknown metrics render with their raw key rather than raising: this is display code, and a
    condition that somehow references an unregistered series should still be *readable* while
    ``validate_series`` is what refuses to let one be written.
    """
    definition = CONSOLIDATION_SERIES.get(condition.metric)
    label = definition.label if definition else condition.metric
    unit = definition.unit if definition else ""

    if condition.threshold is None:
        clause = f"{label} {_DIRECTION_RELATIVE[condition.direction]}"
    else:
        threshold = f"{condition.threshold:g}"
        unit_suffix = f" {unit}" if unit else ""
        clause = (
            f"{label} {_DIRECTION_WITH_THRESHOLD[condition.direction]} "
            f"{threshold}{unit_suffix}"
        )

    days = "day" if condition.min_count == 1 else "days"
    occurrence = (
        f"on any {days} in a {condition.window_days}-day window"
        if condition.min_count == 1
        else f"on {condition.min_count} or more {days} in any {condition.window_days}-day window"
    )
    return f"I'll withdraw this if {clause} {occurrence}."
