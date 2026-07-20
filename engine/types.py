"""Pydantic payload registry — one model per memory type (T3, ADR-13.6).

The 04 design rule: **no rigid nutrition columns**. Structure lives in per-type JSONB
payloads with typed *hot fields* (the attributes we query/aggregate on) plus ``extra="allow"``
so a new nutrient or metric is just a new key — never a migration.

This module is the stage-(B) validator of the ingestion pipeline
(docs/engineering/ingestion-transaction-boundaries.md). Validation failure of any event
sends the whole turn to the note fallback, so required fields are kept minimal on purpose.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, ValidationError

__all__ = [
    "MemoryPayload",
    "MealPayload",
    "WorkoutPayload",
    "SleepPayload",
    "BodyScanPayload",
    "WeightPayload",
    "BloodReportPayload",
    "SupplementPayload",
    "NotePayload",
    "InsightPayload",
    "MEMORY_TYPE_REGISTRY",
    "UnknownMemoryType",
    "validate_payload",
    "payload_to_json",
    "ValidationError",
]


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


class InsightPayload(MemoryPayload):
    """Derived tier-2 memory. Minimal in Phase 2; T5 (Phase 5) adds the typed
    ``retraction_condition`` object + deterministic evaluator."""

    hypothesis: str
    evidence_ids: list[str] = Field(default_factory=list)
    pattern_strength: float | None = None


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
