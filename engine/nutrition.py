"""The nutrition estimate: model-authored, **engine-validated**, frozen at write time.

Where the intelligence lives, and where it does not
---------------------------------------------------
The LLM understands food. It recognizes "Chicken Manchurian" without anyone maintaining a
recipe for it, assumes a plausible portion for "some rice", and declines when it genuinely
does not know. That is world knowledge, and no table this project could maintain would ever
match it.

The engine does not second-guess *what* a food is. It checks that the answer is **arithmetically
and physically possible**, computes every total itself, classifies how much the estimate can be
trusted, and freezes the result into the row. Those checks need no food database — they are mass
balance and the Atwater factors, which do not vary by cuisine.

The split, stated once:

============================  ==================================================
LLM                           understanding + estimation (unbounded, fallible)
Engine (this module)          validation + arithmetic + classification (closed)
Glass box                     provenance + assumptions + uncertainty
============================  ==================================================

**Pure.** No database, no model call, no network, no filesystem, no global state — the same
contract ``engine/analytics.py`` holds, and for the same reason: every claim this module makes
must be reproducible from its arguments alone. The one impurity is deliberate and injected: the
caller supplies ``EstimateMethod`` (including the timestamp), so no clock is read here.

**Frozen at write time (the determinism guarantee).** A model asked twice about "3 eggs" answers
217 kcal and 225 kcal — measured on this project's own cluster, and the reason macro coverage
used to be a coin flip. That variance is confined to *one* moment: ingestion. Once written, the
number is a stored fact, and every aggregation afterwards is pure SQL over stored values. Two
identical meals logged on different days may differ slightly; that is honest, visible in their
ranges, and no different from two real meals differing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "PIPELINE",
    "EstimateMethod",
    "build_nutrition",
    "confidence_class",
    "is_engine_authored",
    "MACRO_KEYS",
    "QTY_BASES",
    "FOOD_KINDS",
    "CONFIDENCE_CLASSES",
]

#: Identity of this pipeline, stored on every estimate it writes. The backfill rewrites a row
#: **only** when it recognizes this value (§ ``is_engine_authored``), which is what makes
#: "never overwrite a user-provided nutrition value" a structural property rather than a rule
#: someone has to remember. Bump the version when a change would produce different numbers from
#: the same input; that is also the signal for a recompute sweep.
PIPELINE = "engine.nutrition/v1"

MACRO_KEYS: tuple[str, ...] = ("protein_g", "carbs_g", "fat_g", "kcal")
#: How a quantity came to be known. ``stated`` is the user's own number; ``ai_estimated`` is a
#: portion the model assumed. Keeping these apart is the distinction the feature exists for.
QTY_BASES: frozenset[str] = frozenset({"stated", "ai_estimated"})
FOOD_KINDS: frozenset[str] = frozenset({"ingredient", "dish", "packaged"})
CONFIDENCE_CLASSES: tuple[str, ...] = ("high", "medium", "low")

# ── physical bounds (no food knowledge required) ───────────────────────────────────────
# These are the only things the engine asserts about food, and each is a fact about matter
# rather than about cuisine — which is precisely why none of them needs a curated table.

#: Atwater factors: kcal per gram of protein, carbohydrate, fat. Textbook constants.
_KCAL_PER_G = {"protein_g": 4.0, "carbs_g": 4.0, "fat_g": 9.0}

#: No whole food is more than ~90% protein by mass (isolate powders approach it; nothing a
#: person calls a meal exceeds it). A model claiming 300 g of protein from 200 g of chicken has
#: made an arithmetic error, and the engine must not store it.
_MAX_PROTEIN_FRACTION = 0.9

#: Macronutrient mass cannot exceed the food's mass. The 5% slack absorbs rounding in the
#: model's own numbers rather than admitting genuinely impossible values.
_MAX_MACRO_MASS_FACTOR = 1.05

#: How far a reported ``kcal`` may sit from the Atwater sum of its own macros before the engine
#: stops believing it. Generous, because fibre, alcohol and rounding all legitimately move it;
#: past this the model has contradicted itself and its arithmetic is replaced, not its food.
_KCAL_TOLERANCE = 0.25

#: An upper bound on a single component's mass. Not a nutritional judgment — a guard against a
#: model emitting grams where it meant something else (a 40 kg "serving" of rice).
_MAX_QTY_G = 5000.0

# ── the confidence matrix (a pure function of two enums) ────────────────────────────────
# Deliberately not a float. A decimal invites arithmetic nobody can justify and tempts the
# system into treating a model's self-report as a measurement; three classes derived from two
# recorded facts can always be explained in one sentence to the person reading the answer.
_CONFIDENCE_MATRIX: dict[tuple[str, str], str] = {
    ("stated", "ingredient"): "high",
    ("stated", "packaged"): "high",
    ("stated", "dish"): "medium",
    ("ai_estimated", "ingredient"): "medium",
    ("ai_estimated", "packaged"): "medium",
    ("ai_estimated", "dish"): "low",
}
_CONFIDENCE_RANK = {name: i for i, name in enumerate(CONFIDENCE_CLASSES)}  # high=0 … low=2


@dataclass(frozen=True, slots=True)
class EstimateMethod:
    """Which pipeline, model and prompt produced an estimate, and when.

    Injected rather than discovered so this module stays pure — and required rather than
    optional because a nutrition value is provider- and prompt-dependent. A stored number whose
    origin is unrecorded cannot be audited, reproduced, or safely recomputed later.
    """

    model_id: str
    prompt_version: str
    estimated_at: datetime
    pipeline: str = PIPELINE

    def to_json(self) -> dict:
        return {
            "pipeline": self.pipeline,
            "model_id": self.model_id,
            "prompt_version": self.prompt_version,
            "estimated_at": self.estimated_at.isoformat(),
        }


def is_engine_authored(nutrition: Any) -> bool:
    """Whether this ``nutrition`` object was written by this pipeline.

    The single guard behind "never overwrite a user-provided nutrition value". Anything this
    pipeline did not author — a value the user stated, a reviewed replay-table macro set, an
    estimate from a future pipeline — answers False and is left exactly as it is.
    """
    if not isinstance(nutrition, dict):
        return False
    method = nutrition.get("method")
    if not isinstance(method, dict):
        return False
    pipeline = method.get("pipeline")
    return isinstance(pipeline, str) and pipeline.split("/", 1)[0] == PIPELINE.split("/", 1)[0]


def confidence_class(qty_basis: str, kind: str) -> str:
    """How much a component's estimate can be trusted, from *how it was known* alone.

    Two recorded enums in, one of three classes out. Unknown combinations fall to ``low``, which
    is the safe direction: a component the engine cannot characterize must never be presented as
    a confident number.
    """
    return _CONFIDENCE_MATRIX.get((qty_basis, kind), "low")


def build_nutrition(
    raw_components: list[dict] | None, *, method: EstimateMethod
) -> dict | None:
    """Validate model-authored components and roll them into a ``payload.nutrition`` object.

    Returns ``None`` only when there was nothing to judge (no components at all) — that is the
    "no estimate happened" signal the caller stores as an absent key, leaving the row eligible
    for a later backfill. Anything else yields an object, **including the all-excluded case**:
    a meal where every food was declined is a fact worth storing, not an absence to retry
    forever.

    Three properties this function guarantees, each of which the model is not trusted to provide:

    * **Totals are computed here.** A model-supplied total is never read. It would be a fourth
      number that could disagree with the three it summarizes.
    * **Impossible components are dropped, never repaired into plausibility.** A dropped
      component becomes an ``unresolved`` entry with a reason, so the exclusion is visible in
      the answer rather than absorbed into a smaller total.
    * **Totals are absent when nothing was counted.** Storing ``protein_g: 0`` for a meal the
      engine could not estimate would make it a real zero to every aggregate — dragging averages
      down and asserting the user ate no protein. Absent is the only honest encoding, and it is
      exactly what the aggregate's ``IS NOT NULL`` filter already understands.
    """
    if not raw_components:
        return None

    counted: list[dict] = []
    unresolved: list[dict] = []

    for raw in raw_components:
        if not isinstance(raw, dict):
            logger.info("nutrition: dropping non-object component %r", raw)
            continue
        component, failure = _validate_component(raw)
        if component is not None:
            counted.append(component)
        elif failure is not None:
            unresolved.append(failure)

    if not counted and not unresolved:
        return None

    nutrition: dict[str, Any] = {
        "estimated": True,
        "coverage": {"counted": len(counted), "excluded": len(unresolved)},
        "method": method.to_json(),
        "components": counted,
    }
    if unresolved:
        nutrition["unresolved"] = unresolved

    if counted:
        nutrition.update(_totals(counted))
        nutrition["basis"] = _basis_rollup(counted)
        nutrition["confidence_class"] = _weakest_class(counted)
        nutrition["estimated"] = nutrition["basis"] != "user_stated"
        band = _range_rollup(counted)
        if band:
            nutrition["range"] = band

    return nutrition


# ── component validation ───────────────────────────────────────────────────────────────
def _validate_component(raw: dict) -> tuple[dict | None, dict | None]:
    """One component in, either a validated component or an ``unresolved`` record out.

    Never both, never neither. Every rejection carries a machine-readable ``reason`` because a
    component that vanished without one is indistinguishable from a component the model never
    mentioned — and the difference matters to the person reading the total.
    """
    item = _clean_str(raw.get("item"))
    if not item:
        return None, None  # nothing to even name in an exclusion notice

    # The model's own decline. Respected verbatim: "I don't know this food" is a valid answer
    # and is the reason the system never has to invent one.
    if not raw.get("resolved", False):
        return None, _unresolved(item, raw.get("reason") or "unrecognized_food", raw)

    qty_g = _positive_float(raw.get("qty_g"))
    if qty_g is None or qty_g > _MAX_QTY_G:
        return None, _unresolved(item, "invalid_quantity", raw)

    qty_basis = _clean_str(raw.get("qty_basis"))
    if qty_basis not in QTY_BASES:
        return None, _unresolved(item, "invalid_quantity_basis", raw)

    protein = _non_negative_float(raw.get("protein_g"))
    if protein is None:
        # protein_g is the aggregated metric; a component without it has nothing to contribute
        # to the question this feature exists to answer.
        return None, _unresolved(item, "incomplete_macros", raw)
    carbs = _non_negative_float(raw.get("carbs_g")) or 0.0
    fat = _non_negative_float(raw.get("fat_g")) or 0.0

    # ── the physical bounds ──
    if protein > _MAX_PROTEIN_FRACTION * qty_g:
        logger.info("nutrition: %r claims %.1fg protein from %.1fg food", item, protein, qty_g)
        return None, _unresolved(item, "implausible_protein", raw)
    if protein + carbs + fat > _MAX_MACRO_MASS_FACTOR * qty_g:
        logger.info("nutrition: %r macro mass exceeds its own %.1fg", item, qty_g)
        return None, _unresolved(item, "implausible_macro_mass", raw)

    # ── energy coherence: the model must agree with its own macros ──
    atwater = sum(_KCAL_PER_G[k] * v for k, v in
                  (("protein_g", protein), ("carbs_g", carbs), ("fat_g", fat)))
    reported = _non_negative_float(raw.get("kcal"))
    kcal, recomputed = _reconcile_kcal(reported, atwater)

    kind = _clean_str(raw.get("kind")) or "dish"
    if kind not in FOOD_KINDS:
        # An unfamiliar kind is not a rejection — it is an unknown, and the confidence matrix
        # already treats unknown combinations as `low`.
        kind = "dish"

    component: dict[str, Any] = {
        "item": item,
        "kind": kind,
        "qty_g": round(qty_g, 2),
        "qty_basis": qty_basis,
        "nutrition_basis": "ai_estimated",
        "protein_g": round(protein, 2),
        "carbs_g": round(carbs, 2),
        "fat_g": round(fat, 2),
        "kcal": round(kcal, 1),
        "confidence_class": confidence_class(qty_basis, kind),
    }
    if recomputed:
        component["kcal_recomputed"] = True

    understood_as = _clean_str(raw.get("understood_as"))
    if understood_as:
        component["understood_as"] = understood_as
    qty_note = _clean_str(raw.get("qty_note"))
    if qty_note:
        component["qty_note"] = qty_note

    assumptions = _clean_str_list(raw.get("assumptions"))
    if assumptions:
        component["assumptions"] = assumptions

    band = _component_range(raw.get("range"), component)
    if band:
        component["range"] = band

    model_confidence = _non_negative_float(raw.get("model_confidence"))
    if model_confidence is not None:
        # Carried for display only. Nothing below this line ever reads it back.
        component["model_confidence"] = round(min(1.0, model_confidence), 2)

    return component, None


def _reconcile_kcal(reported: float | None, atwater: float) -> tuple[float, bool]:
    """Trust the model's kcal only when it agrees with the model's own macros.

    Disagreement past the tolerance is self-contradiction, and the macros win: they are what the
    aggregated metric is built from, so an energy figure that contradicts them would make one
    row assert two different meals. The substitution is recorded, never silent.
    """
    if reported is None:
        return atwater, True
    if atwater <= 0:
        return reported, False
    if abs(reported - atwater) > _KCAL_TOLERANCE * atwater:
        return atwater, True
    return reported, False


def _component_range(raw_range: Any, component: dict) -> dict[str, list[float]] | None:
    """Validate the model's uncertainty bands, widening each to contain its own point estimate.

    A range that excludes the number it qualifies is malformed rather than informative; widening
    (instead of dropping) keeps the honest signal — that the model was unsure — while making the
    pair internally consistent.
    """
    if not isinstance(raw_range, dict):
        return None
    out: dict[str, list[float]] = {}
    for key in MACRO_KEYS:
        pair = raw_range.get(key)
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            continue
        low, high = _non_negative_float(pair[0]), _non_negative_float(pair[1])
        if low is None or high is None:
            continue
        if low > high:
            low, high = high, low
        point = component.get(key)
        if isinstance(point, (int, float)):
            low, high = min(low, float(point)), max(high, float(point))
        out[key] = [round(low, 2), round(high, 2)]
    return out or None


# ── rollups (the engine's own arithmetic — model totals are never read) ─────────────────
def _totals(counted: list[dict]) -> dict[str, float]:
    totals: dict[str, float] = {}
    for key in MACRO_KEYS:
        total = sum(float(c[key]) for c in counted if isinstance(c.get(key), (int, float)))
        totals[key] = round(total, 1 if key == "kcal" else 2)
    return totals


def _basis_rollup(counted: list[dict]) -> str:
    """``user_stated`` only when every counted component is; ``ai_estimated`` when none is."""
    bases = {c.get("nutrition_basis", "ai_estimated") for c in counted}
    if bases == {"user_stated"}:
        return "user_stated"
    if bases == {"ai_estimated"}:
        return "ai_estimated"
    return "mixed"


def _weakest_class(counted: list[dict]) -> str:
    """The meal is only as trustworthy as its shakiest component.

    Averaging the classes would let three exact ingredients bury one guessed restaurant dish —
    which is precisely the case where the reader most needs the warning.
    """
    return max(
        (c.get("confidence_class", "low") for c in counted),
        key=lambda name: _CONFIDENCE_RANK.get(name, len(CONFIDENCE_CLASSES)),
    )


def _range_rollup(counted: list[dict]) -> dict[str, list[float]]:
    """Sum the component bands into a meal-level band.

    A component with no band contributes its point estimate to both ends — it narrows the total's
    uncertainty without pretending to widen it. Emitted only when at least one component actually
    carried a range, so a fully exact meal shows no band rather than a zero-width one.
    """
    out: dict[str, list[float]] = {}
    for key in MACRO_KEYS:
        low = high = 0.0
        any_band = False
        for component in counted:
            point = component.get(key)
            point = float(point) if isinstance(point, (int, float)) else 0.0
            band = component.get("range")
            pair = band.get(key) if isinstance(band, dict) else None
            if isinstance(pair, list) and len(pair) == 2:
                any_band = True
                low += float(pair[0])
                high += float(pair[1])
            else:
                low += point
                high += point
        if any_band:
            out[key] = [round(low, 2), round(high, 2)]
    return out


# ── coercion helpers (tolerant of a model's shapes, strict about validity) ──────────────
def _unresolved(item: str, reason: str, raw: dict) -> dict:
    record = {"item": item, "reason": reason}
    question = _clean_str(raw.get("clarifying_question"))
    if question:
        # Stored and displayed; v1 does not build a follow-up conversation flow around it.
        record["clarifying_question"] = question
    return record


def _clean_str(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _clean_str_list(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [s for s in (_clean_str(v) for v in value) if s]


def _positive_float(value: Any) -> float | None:
    number = _non_negative_float(value)
    return number if number is not None and number > 0 else None


def _non_negative_float(value: Any) -> float | None:
    """Accept ints, floats, and numeric strings; reject bools, NaN, inf, and negatives.

    ``bool`` is excluded explicitly because it is a subclass of ``int`` in Python — ``True``
    would otherwise sail through as ``1.0`` gram of protein.
    """
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, str):
        try:
            value = float(value.strip())
        except ValueError:
            return None
    if not isinstance(value, (int, float)):
        return None
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")) or number < 0:
        return None
    return number
