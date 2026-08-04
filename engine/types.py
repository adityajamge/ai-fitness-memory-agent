"""Pydantic payload registry — one model per memory type (T3, ADR-13.6).

The 04 design rule: **no rigid nutrition columns**. Structure lives in per-type JSONB
payloads with typed *hot fields* (the attributes we query/aggregate on) plus ``extra="allow"``
so a new nutrient or metric is just a new key — never a migration.

This module is the stage-(B) validator of the ingestion pipeline
(docs/engineering/ingestion-transaction-boundaries.md). Validation failure of any event
sends the whole turn to the note fallback, so required fields are kept minimal on purpose.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

__all__ = [
    "MemoryPayload",
    "EffectScale",
    "MealPayload",
    "WorkoutPayload",
    "SleepPayload",
    "BodyScanPayload",
    "WeightPayload",
    "BloodReportPayload",
    "SupplementPayload",
    "NotePayload",
    "InsightPayload",
    "RetractionCondition",
    "INSIGHT_KINDS",
    "SERIES_KINDS",
    "MAX_EVIDENCE_IDS",
    "STRENGTH_TOLERANCE",
    "MEMORY_TYPE_REGISTRY",
    "UnknownMemoryType",
    "validate_payload",
    "payload_to_json",
    "ValidationError",
]

#: The registered insight kinds (invariant **I-1**). Adding one is a reviewed, ADR-recorded
#: act, not an implementation detail — which is why the payload field below is a ``Literal``
#: rather than a free string, and why this frozenset is derived from it rather than the other
#: way round. Definitions: consolidation-architecture.md §4.1.
INSIGHT_KINDS: frozenset[str] = frozenset({"level_shift", "intervention_outcome"})

#: What a series *is about*: a behaviour the user controls, or an outcome they measure
#: (§4.4). It decides which detector may run on it.
SERIES_KINDS: frozenset[str] = frozenset({"behavioural", "outcome"})

#: Lineage cap (§4.2, invariant **I-5**). An insight cites the boundary rows of each
#: contributing segment plus every contributing point event, up to this many — a 105-row
#: supplement segment would otherwise produce a lineage the Phase 6 graph cannot render.
#: ``evidence_count`` always carries the *true* total, which is what keeps the cap honest.
MAX_EVIDENCE_IDS = 24

#: Float slack when checking ``pattern_strength == effect × coverage × specificity``.
#: Components are stored at full precision, so this absorbs IEEE rounding only.
STRENGTH_TOLERANCE = 1e-6


@dataclass(frozen=True, slots=True)
class EffectScale:
    """How big a change has to be, **in the metric's own units** (consolidation-architecture
    §4.13, invariant **I-24**).

    One scale per consolidatable series, because relative change is the wrong yardstick for a
    physiologically bounded quantity. Vitamin D moves in multiples — 6.2 → 38.4 is 5.2× — while
    body fat and weight move in small fractions of themselves and always will. A single relative
    floor across both either refuses every real body-composition change or admits pure
    measurement noise in the markers; there is no value that does neither, because they are not
    the same kind of quantity.

    Each field answers exactly one question, which is what makes the pair explainable:

    ``min_delta``
        *Is this bigger than our measurement noise?* The gate. Below it the engine says nothing
        (**I-22**) — a BIA scale that varies ±1–2 points with hydration cannot support a claim
        about a 0.8-point move, and the account's own body-scan payload carries that caveat.

    ``full_delta``
        *What does a full-size change look like?* The denominator of ``effect``, so a change of
        this size scores 1.0 and the score means the same thing in every series. Without it a
        real 3.2-point body-fat drop would publish as ``effect = 0.08`` beside a diet change at
        0.84 — the engine calling a genuine change flimsy, which is a worse failure than
        refusing to speak at all.

    **These are product heuristics, not clinical thresholds** (ADR-13.12, **I-2**). They state
    what *this application* treats as a meaningful or full-size change, anchored to measurement
    precision and to the size of change a person would describe as real. Borrowing clinical
    cutoffs would imply an authority the engine does not have — the same line that keeps a
    pattern strength from being called a probability. Every value ships with its basis written
    beside it in ``engine/insights.py``; changing one is a reviewed product judgment.
    """

    min_delta: float
    full_delta: float

    def __post_init__(self) -> None:
        if self.min_delta <= 0:
            raise ValueError(f"min_delta must be positive, got {self.min_delta}")
        if self.full_delta < self.min_delta:
            raise ValueError(
                f"full_delta {self.full_delta} is below min_delta {self.min_delta}; a full-size "
                "change cannot be smaller than the smallest change worth reporting"
            )

    def gates(self, delta: float) -> bool:
        """Whether a change clears the noise floor and is worth a claim."""
        return abs(delta) >= self.min_delta

    def effect(self, delta: float) -> float:
        """``min(1, |Δ| / full_delta)`` — the share of a full-size change, capped at one."""
        return min(1.0, abs(delta) / self.full_delta)


class MemoryPayload(BaseModel):
    """Base for every payload: unknown keys are preserved (migration-free evolution)."""

    model_config = ConfigDict(extra="allow")


# ── nested value objects ──────────────────────────────────────────────────────────────
class MealItem(MemoryPayload):
    name: str
    qty_g: float | None = None
    qty: float | None = None


class Nutrition(MemoryPayload):
    # A new nutrient tomorrow is just another key here — extra="allow" keeps it.
    protein_g: float | None = None
    carbs_g: float | None = None
    fat_g: float | None = None
    kcal: float | None = None
    estimated: bool | None = None


# ── per-type payloads (hot fields typed; everything else allowed) ──────────────────────
class MealPayload(MemoryPayload):
    meal_type: str | None = None
    items: list[MealItem] = Field(default_factory=list)
    nutrition: Nutrition | None = None
    photo_s3_key: str | None = None


class WorkoutPayload(MemoryPayload):
    activity: str | None = None
    duration_min: float | None = None
    distance_km: float | None = None
    exercises: list[dict] | None = None


class SleepPayload(MemoryPayload):
    hours: float | None = None
    quality: str | None = None


class BodyScanPayload(MemoryPayload):
    body_fat_pct: float | None = None
    weight_kg: float | None = None
    method: str | None = None


class WeightPayload(MemoryPayload):
    weight_kg: float | None = None


class BloodReportPayload(MemoryPayload):
    panel: str | None = None
    markers: dict[str, float] | None = None  # e.g. {"ldl": 96.0, "hba1c": 5.4}


class SupplementPayload(MemoryPayload):
    name: str | None = None
    dose_mg: float | None = None


class NotePayload(MemoryPayload):
    # Required: the note is how we honor never-lose-input, so it must carry the raw text.
    text: str


class RetractionCondition(MemoryPayload):
    """What would make the engine withdraw a claim (ADR-13.11, pinned by §4.14).

    ``direction`` is the movement that would **contradict** the insight. ``threshold`` is the
    optional comparator half — ADR-13.11's "comparator/direction" was one field or two
    depending on how you read it, and the answer is: the comparator exists when there is
    something absolute to compare against, and ``direction`` alone otherwise.

    A **counterexample** is an observation of ``metric`` inside the trailing ``window_days``
    that either (no threshold) moves in ``direction`` relative to the insight's post-shift
    level — its later measurement, for an ``intervention_outcome`` — or (with a threshold)
    crosses that threshold in ``direction``. ``count >= min_count`` retracts. Evaluation is
    pure arithmetic over typed fields with no model call (**I-21**), and retraction flips
    ``status`` without deleting or rewriting anything (ADR-9, **I-20**).

    Prose is **rendered from this object** and never stored as the condition — see
    ``engine.insights.render_retraction_condition``. Storing the sentence would make the
    displayed rule and the evaluated rule two things that can disagree.
    """

    metric: str
    direction: Literal["rising", "falling"]
    window_days: int
    min_count: int
    threshold: float | None = None

    @model_validator(mode="after")
    def _positive_bounds(self) -> RetractionCondition:
        if self.window_days < 1:
            raise ValueError("window_days must be >= 1")
        if self.min_count < 1:
            raise ValueError("min_count must be >= 1")
        return self


class InsightPayload(MemoryPayload):
    """Derived tier-2 memory — a claim the user never made, so every field that makes it
    auditable is required (consolidation-architecture.md §4.1, §4.6, §4.12, §4.13).

    ``kind`` is closed (**I-1**). ``series_metric`` names a ``CONSOLIDATION_SERIES`` entry;
    membership is checked by ``engine.insights.validate_series`` rather than here, because a
    payload model that imported the series vocabulary would invert the layering this module
    depends on (see ``engine/insights.py``'s note on the one-way dependency).

    ``window_start``/``window_end`` are what the claim is *about*; the row's ``event_time`` is
    ``window_end`` and its ``created_at`` stays truthful (§4.12, ADR-13.10) — an insight
    derived today about May is not back-dated.

    **The three strength components are required whenever a score is published** (**I-19**),
    and the score must equal their product. That is enforced here rather than trusted, because
    a ``pattern_strength`` whose components do not explain it is exactly the
    unfalsifiable-number failure ADR-13.12 exists to prevent. Computing the factors is the
    detector's job (§4.13); agreeing with them is this contract's.
    """

    kind: Literal["level_shift", "intervention_outcome"]
    hypothesis: str
    series_metric: str
    series_kind: Literal["behavioural", "outcome"]
    window_start: datetime
    window_end: datetime
    #: The values the claim is *about* — the level before and after, or the earlier and later
    #: measurement. Typed rather than left to ``extra="allow"`` because the retraction evaluator
    #: **branches on** ``post_value`` (it is the reference a direction-only condition compares
    #: against, §4.14), and a hot field a deterministic evaluator reads cannot be an untyped
    #: extra that might arrive as a string. They are also what makes an insight self-describing:
    #: without them the numbers exist only inside ``hypothesis``, which no deterministic code
    #: may parse (**I-8**).
    pre_value: float
    post_value: float
    #: Boundary-anchored and capped (§4.2). Never the complete set for an expanded period —
    #: ``evidence_count`` is.
    evidence_ids: list[str] = Field(min_length=1)
    #: The true number of contributing memories, capped or not. Rendering this beside a capped
    #: lineage is what stops the cap from quietly under-reporting the evidence.
    evidence_count: int
    effect: float
    coverage: float
    specificity: float
    pattern_strength: float
    #: Deterministic identity of the *claim* (§4.6). Stable under rewording, changes on a value
    #: change — which is what makes "recompute wrote nothing" decidable without a deep compare.
    fingerprint: str
    retraction_condition: RetractionCondition | None = None

    @model_validator(mode="after")
    def _coherent(self) -> InsightPayload:
        for name in ("effect", "coverage", "specificity", "pattern_strength"):
            value = getattr(self, name)
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be within [0, 1], got {value}")
        product = self.effect * self.coverage * self.specificity
        if abs(self.pattern_strength - product) > STRENGTH_TOLERANCE:
            raise ValueError(
                f"pattern_strength {self.pattern_strength} does not equal "
                f"effect × coverage × specificity ({product}); a published score must be "
                "explained by its components (I-19)"
            )
        if self.window_end < self.window_start:
            raise ValueError("window_end must not precede window_start")
        if len(self.evidence_ids) > MAX_EVIDENCE_IDS:
            raise ValueError(f"evidence_ids exceeds the {MAX_EVIDENCE_IDS}-id lineage cap")
        if self.evidence_count < len(self.evidence_ids):
            raise ValueError(
                f"evidence_count {self.evidence_count} is below the {len(self.evidence_ids)} "
                "ids cited; it is the true total, never the truncated one"
            )
        return self


MEMORY_TYPE_REGISTRY: dict[str, type[MemoryPayload]] = {
    "meal": MealPayload,
    "workout": WorkoutPayload,
    "sleep": SleepPayload,
    "body_scan": BodyScanPayload,
    "weight": WeightPayload,
    "blood_report": BloodReportPayload,
    "supplement": SupplementPayload,
    "note": NotePayload,
    "insight": InsightPayload,
}


class UnknownMemoryType(ValueError):
    """Raised when a memory type is not in the registry (treated as a validation failure —
    the ingestion pipeline falls back to a note)."""


def validate_payload(mem_type: str, payload: dict) -> MemoryPayload:
    """Validate a raw payload dict against its type's model.

    Raises UnknownMemoryType for unregistered types and pydantic ValidationError when a
    typed hot field is the wrong type or a required field is missing. Both are caught by
    the ingestion pipeline's stage (B) and route the turn to the note fallback.
    """
    model = MEMORY_TYPE_REGISTRY.get(mem_type)
    if model is None:
        raise UnknownMemoryType(mem_type)
    return model.model_validate(payload)


def payload_to_json(model: MemoryPayload) -> dict:
    """Serialize a validated payload to a JSONB-ready dict, dropping only ``None`` hot
    fields (extras and explicit values, incl. ``False``/``0``, are preserved)."""
    return model.model_dump(mode="json", exclude_none=True)
