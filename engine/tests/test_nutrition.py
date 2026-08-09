"""The nutrition validation kernel (``engine/nutrition.py``).

Pure unit tests — no database, no model, no clock. These pin the half of the feature the LLM
is *not* trusted with: that impossible numbers are refused, that totals are the engine's own
arithmetic, that "we could not estimate this" survives into storage as a fact, and that the
three quantity bases stay distinguishable all the way to the payload.

The organizing question throughout: **can a wrong model answer become a confident wrong number
in the database?** Every test below is one route by which it must not.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from engine.nutrition import (
    PIPELINE,
    EstimateMethod,
    build_nutrition,
    confidence_class,
    is_engine_authored,
)

METHOD = EstimateMethod(
    model_id="test-model",
    prompt_version="nutrition/test",
    estimated_at=datetime(2026, 8, 9, 19, 44, tzinfo=timezone.utc),
)


def _component(**overrides) -> dict:
    """A well-formed, resolved component — the baseline each test perturbs one field of."""
    base = {
        "item": "chicken",
        "resolved": True,
        "understood_as": "chicken breast, cooked",
        "kind": "ingredient",
        "qty_g": 200,
        "qty_basis": "stated",
        "protein_g": 62.0,
        "carbs_g": 0.0,
        "fat_g": 7.2,
        "kcal": 313.0,
    }
    base.update(overrides)
    return base


# ── the three bases (the distinction the whole feature exists to keep) ─────────────────
def test_stated_quantity_is_recorded_as_stated() -> None:
    """'200 g chicken' — the user fixed the amount, so the quantity is not a guess."""
    nutrition = build_nutrition([_component()], method=METHOD)

    assert nutrition is not None
    component = nutrition["components"][0]
    assert component["qty_basis"] == "stated"
    assert component["qty_g"] == 200
    assert component["confidence_class"] == "high"
    assert nutrition["protein_g"] == 62.0
    assert nutrition["coverage"] == {"counted": 1, "excluded": 0}


def test_ai_estimated_quantity_is_marked_and_downgrades_confidence() -> None:
    """'some rice' — the model chose the portion, and the payload must say so out loud."""
    nutrition = build_nutrition(
        [
            _component(
                item="rice",
                understood_as="white rice, cooked",
                qty_g=150,
                qty_basis="ai_estimated",
                qty_note="'some rice' — assumed one katori ≈ 150 g cooked",
                protein_g=4.0,
                carbs_g=40.0,
                fat_g=0.4,
                kcal=180.0,
            )
        ],
        method=METHOD,
    )

    assert nutrition is not None
    component = nutrition["components"][0]
    assert component["qty_basis"] == "ai_estimated"
    assert component["confidence_class"] == "medium"  # estimated qty, but a known ingredient
    assert "katori" in component["qty_note"]
    assert nutrition["estimated"] is True


def test_unknown_food_is_excluded_never_invented() -> None:
    """A decline is respected verbatim: no macros, no contribution, and a stored reason.

    This is the property that lets the system have no food database at all — it is allowed not
    to know, as long as not-knowing is visible."""
    nutrition = build_nutrition(
        [
            _component(),
            {
                "item": "grandma's special curry",
                "resolved": False,
                "reason": "unrecognized_dish",
                "clarifying_question": "What's in it, and roughly how much did you have?",
            },
        ],
        method=METHOD,
    )

    assert nutrition is not None
    assert nutrition["coverage"] == {"counted": 1, "excluded": 1}
    assert nutrition["protein_g"] == 62.0  # the chicken only — the curry contributes nothing
    (unresolved,) = nutrition["unresolved"]
    assert unresolved["item"] == "grandma's special curry"
    assert unresolved["reason"] == "unrecognized_dish"
    assert "roughly how much" in unresolved["clarifying_question"]


def test_all_foods_unknown_stores_no_totals_rather_than_zero() -> None:
    """A meal the engine could not estimate must have **no** ``protein_g``, not ``0``.

    Zero would be a real value to every aggregate — dragging averages down and asserting the
    user ate no protein. Absent is the only honest encoding, and it is exactly what the
    aggregate's ``IS NOT NULL`` filter already understands."""
    nutrition = build_nutrition(
        [{"item": "mystery dish", "resolved": False, "reason": "unrecognized_dish"}],
        method=METHOD,
    )

    assert nutrition is not None
    assert "protein_g" not in nutrition
    assert nutrition["coverage"] == {"counted": 0, "excluded": 1}
    assert nutrition["components"] == []


# ── the engine's own arithmetic ────────────────────────────────────────────────────────
def test_totals_are_summed_by_the_engine() -> None:
    """Totals are computed here and never read from the model — a model-supplied total would
    be a fourth number free to disagree with the three it summarizes."""
    nutrition = build_nutrition(
        [
            _component(protein_g=62.0, carbs_g=0.0, fat_g=7.2, kcal=313.0),
            _component(item="roti", kind="ingredient", qty_g=120, protein_g=9.6,
                       carbs_g=54.0, fat_g=3.6, kcal=287.0),
        ],
        method=METHOD,
    )

    assert nutrition is not None
    assert nutrition["protein_g"] == pytest.approx(71.6)
    assert nutrition["carbs_g"] == pytest.approx(54.0)
    assert nutrition["kcal"] == pytest.approx(600.0)


def test_meal_confidence_is_the_weakest_component() -> None:
    """Three exact ingredients must not bury one guessed restaurant dish — that is precisely
    the case where the reader most needs the warning."""
    nutrition = build_nutrition(
        [
            _component(),  # high
            _component(item="chicken manchurian", kind="dish", qty_basis="ai_estimated",
                       qty_g=250, protein_g=22.0, carbs_g=28.0, fat_g=19.0, kcal=371.0),
        ],
        method=METHOD,
    )

    assert nutrition is not None
    assert nutrition["confidence_class"] == "low"


@pytest.mark.parametrize(
    ("qty_basis", "kind", "expected"),
    [
        ("stated", "ingredient", "high"),
        ("stated", "packaged", "high"),
        ("stated", "dish", "medium"),
        ("ai_estimated", "ingredient", "medium"),
        ("ai_estimated", "dish", "low"),
        ("nonsense", "dish", "low"),  # unknown combinations fall to the safe direction
    ],
)
def test_confidence_matrix(qty_basis: str, kind: str, expected: str) -> None:
    assert confidence_class(qty_basis, kind) == expected


# ── refusing impossible numbers (physics, not food knowledge) ──────────────────────────
def test_protein_exceeding_food_mass_is_rejected() -> None:
    """No whole food is 90%+ protein. 300 g of protein from 200 g of chicken is an arithmetic
    error, and storing it would poison every aggregate that touched the day."""
    nutrition = build_nutrition([_component(protein_g=300.0)], method=METHOD)

    assert nutrition is not None
    assert nutrition["coverage"]["counted"] == 0
    assert nutrition["unresolved"][0]["reason"] == "implausible_protein"


def test_macro_mass_exceeding_food_mass_is_rejected() -> None:
    nutrition = build_nutrition(
        [_component(protein_g=80.0, carbs_g=80.0, fat_g=80.0)], method=METHOD
    )

    assert nutrition is not None
    assert nutrition["unresolved"][0]["reason"] == "implausible_macro_mass"


def test_incoherent_kcal_is_recomputed_from_the_macros_and_flagged() -> None:
    """When the model contradicts itself, the macros win — they are what the aggregated metric
    is built from, so an energy figure that disagrees would make one row assert two meals. The
    substitution is recorded rather than silent."""
    nutrition = build_nutrition(
        [_component(protein_g=62.0, carbs_g=0.0, fat_g=7.2, kcal=9000.0)], method=METHOD
    )

    assert nutrition is not None
    component = nutrition["components"][0]
    assert component["kcal"] == pytest.approx(4 * 62.0 + 9 * 7.2, rel=1e-3)
    assert component["kcal_recomputed"] is True


def test_missing_protein_excludes_the_component() -> None:
    nutrition = build_nutrition([_component(protein_g=None)], method=METHOD)

    assert nutrition is not None
    assert nutrition["unresolved"][0]["reason"] == "incomplete_macros"


@pytest.mark.parametrize("qty", [0, -5, None, "lots", 99999])
def test_invalid_quantity_excludes_the_component(qty: object) -> None:
    nutrition = build_nutrition([_component(qty_g=qty)], method=METHOD)

    assert nutrition is not None
    assert nutrition["unresolved"][0]["reason"] == "invalid_quantity"


def test_invalid_quantity_basis_excludes_the_component() -> None:
    """An unrecognised basis cannot be silently defaulted: 'stated' and 'ai_estimated' are the
    difference between a fact and a guess, and defaulting either way would misreport one."""
    nutrition = build_nutrition([_component(qty_basis="vibes")], method=METHOD)

    assert nutrition is not None
    assert nutrition["unresolved"][0]["reason"] == "invalid_quantity_basis"


def test_booleans_are_not_accepted_as_numbers() -> None:
    """`bool` is a subclass of `int` in Python, so `True` would otherwise sail through as
    1.0 g of protein."""
    nutrition = build_nutrition([_component(protein_g=True)], method=METHOD)

    assert nutrition is not None
    assert nutrition["coverage"]["counted"] == 0


# ── ranges: uncertainty preserved, never manufactured ──────────────────────────────────
def test_range_is_widened_to_contain_its_own_point_estimate() -> None:
    """A band that excludes the number it qualifies is malformed rather than informative.
    Widening keeps the honest signal (the model was unsure) while making the pair consistent."""
    nutrition = build_nutrition(
        [_component(protein_g=62.0, range={"protein_g": [10.0, 20.0]})], method=METHOD
    )

    assert nutrition is not None
    assert nutrition["components"][0]["range"]["protein_g"] == [10.0, 62.0]


def test_meal_range_sums_components_and_is_absent_when_nothing_is_banded() -> None:
    banded = build_nutrition(
        [
            _component(range={"protein_g": [58.0, 66.0]}),
            _component(item="roti", qty_g=120, protein_g=9.6, carbs_g=54.0, fat_g=3.6,
                       kcal=287.0),
        ],
        method=METHOD,
    )
    assert banded is not None
    # The unbanded roti contributes its point estimate to both ends.
    assert banded["range"]["protein_g"] == [pytest.approx(67.6), pytest.approx(75.6)]

    plain = build_nutrition([_component()], method=METHOD)
    assert plain is not None
    assert "range" not in plain


# ── provenance and the no-overwrite guard ──────────────────────────────────────────────
def test_method_is_recorded_on_every_estimate() -> None:
    """A nutrition value is provider- and prompt-dependent, so a stored number without its
    origin is one nobody can audit or safely recompute."""
    nutrition = build_nutrition([_component()], method=METHOD)

    assert nutrition is not None
    assert nutrition["method"] == {
        "pipeline": PIPELINE,
        "model_id": "test-model",
        "prompt_version": "nutrition/test",
        "estimated_at": "2026-08-09T19:44:00+00:00",
    }


def test_is_engine_authored_recognizes_only_this_pipeline() -> None:
    """The single guard behind 'never overwrite a user-provided nutrition value'."""
    ours = build_nutrition([_component()], method=METHOD)
    assert ours is not None
    assert is_engine_authored(ours) is True

    # A reviewed replay-table value: estimated, but not by us.
    assert is_engine_authored({"protein_g": 46, "estimated": True}) is False
    # A value the user stated.
    assert is_engine_authored({"protein_g": 50, "basis": "user_stated"}) is False
    # A future pipeline's value is still not ours to rewrite.
    assert is_engine_authored({"method": {"pipeline": "vendor.nutrition/v3"}}) is False
    assert is_engine_authored(None) is False
    assert is_engine_authored("nutrition") is False


def test_a_version_bump_stays_engine_authored() -> None:
    """`v1` → `v2` must remain recognisable as ours, or a recompute sweep could never rewrite
    its own earlier output."""
    assert is_engine_authored({"method": {"pipeline": "engine.nutrition/v2"}}) is True


def test_no_components_is_no_estimate() -> None:
    """Distinct from 'estimated, and everything was excluded': this leaves the row eligible for
    backfill instead of marking it as already handled."""
    assert build_nutrition([], method=METHOD) is None
    assert build_nutrition(None, method=METHOD) is None
