"""Nutrition through the write path and back out of an aggregate — stage (B½) end to end.

Runs against real CockroachDB via the ``db`` fixture. Where ``test_nutrition.py`` pins the pure
validation kernel, these pin the parts that only exist once a row is committed: that the
estimate is **frozen into the payload at write time**, that the aggregate finds it with no model
call, that a failed estimate costs nothing but a backfill, and that nothing this pipeline did not
author is ever rewritten.

The first test is the reported bug, reproduced exactly.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from engine.ingestion import IngestionService
from engine.model import ExtractedEvent
from engine.repository import get_memory
from engine.retrieval import AggregateSpec, aggregate_memories
from engine.tests.conftest import FakeModelProvider

TZ = "Asia/Kolkata"
DINNER = datetime(2026, 8, 8, 19, 30, tzinfo=timezone.utc)


def _service(db, provider, **kw) -> IngestionService:
    return IngestionService(db, provider, default_tz=TZ, **kw)


def _fetch(db, user_id, memory_id):
    with db.transaction() as cur:
        return get_memory(cur, user_id, memory_id)


def _aggregate(db, user_id, metric="protein_g", *, days=3):
    """The exact read path "how much protein yesterday?" takes — no model involved."""
    spec = AggregateSpec(
        metric=metric,
        start=DINNER - timedelta(days=days),
        end=DINNER + timedelta(days=days),
        tz=TZ,
        agg="sum",
    )
    with db.transaction() as cur:
        result, _ = aggregate_memories(cur, user_id, spec)
    return result


# ── the reported bug, reproduced ───────────────────────────────────────────────────────
def _reported_dinner() -> ExtractedEvent:
    """Extraction's output for the exact reported turn.

    Note what extraction does and does not produce: items and quantities, with "some" preserved
    in ``qty_text`` and no numeric quantity invented — and **no nutrition key at all**, which is
    now the extractor's contract rather than its mood."""
    return ExtractedEvent(
        type="meal",
        event_time=DINNER,
        tz=TZ,
        confidence=0.7,
        summary="Dinner: 200g chicken, 3 rotis, some rice, and dal",
        payload={
            "meal_type": "dinner",
            "items": [
                {"name": "chicken", "qty_g": 200.0},
                {"name": "rotis", "qty": 3.0},
                {"name": "rice", "qty_text": "some"},
                {"name": "dal", "qty_text": "some"},
            ],
        },
    )


def _reported_dinner_estimate() -> list[dict]:
    """What the nutrition model returns for that meal: two stated quantities, two assumed."""
    return [
        {
            "item": "chicken", "resolved": True, "kind": "ingredient",
            "understood_as": "chicken breast, cooked, skinless",
            "qty_g": 200, "qty_basis": "stated", "qty_note": "user stated 200 g",
            "protein_g": 62.0, "carbs_g": 0.0, "fat_g": 7.2, "kcal": 313.0,
            "assumptions": ["skinless breast", "grilled, no added oil"],
            "model_confidence": 0.9,
        },
        {
            "item": "rotis", "resolved": True, "kind": "ingredient",
            "understood_as": "wheat roti/chapati",
            "qty_g": 120, "qty_basis": "stated", "qty_note": "3 rotis at ~40 g each",
            "protein_g": 9.6, "carbs_g": 54.0, "fat_g": 3.6, "kcal": 287.0,
            "model_confidence": 0.75,
        },
        {
            "item": "rice", "resolved": True, "kind": "ingredient",
            "understood_as": "white rice, cooked",
            "qty_g": 150, "qty_basis": "ai_estimated",
            "qty_note": "'some' — assumed one katori ≈ 150 g cooked",
            "protein_g": 4.0, "carbs_g": 40.0, "fat_g": 0.4, "kcal": 180.0,
            "range": {"protein_g": [2.5, 6.0]},
            "assumptions": ["one standard katori serving"], "model_confidence": 0.55,
        },
        {
            "item": "dal", "resolved": True, "kind": "dish",
            "understood_as": "cooked lentil dal, home-style",
            "qty_g": 150, "qty_basis": "ai_estimated",
            "qty_note": "'some' — assumed one katori ≈ 150 g",
            "protein_g": 9.0, "carbs_g": 20.0, "fat_g": 3.0, "kcal": 143.0,
            "range": {"protein_g": [6.0, 12.0]},
            "assumptions": ["toor/moong dal", "lightly tempered"], "model_confidence": 0.5,
        },
    ]


def test_reported_bug_meal_persists_nutrition_and_answers_the_protein_question(db, user_id):
    """REGRESSION — the exact reported flow.

    "Yesterday, for dinner, I ate 200 gram chicken, 3 rotis, some rice, and dal" previously
    committed with **no nutrition key**, so "how much protein yesterday?" hit an aggregate that
    filters on ``nutrition.protein_g IS NOT NULL``, matched nothing, and answered "there's
    nothing logged for protein yet" — while the meal itself recalled perfectly. Both halves are
    asserted here: the meal is stored *and* the protein question now has a number.
    """
    provider = FakeModelProvider([_reported_dinner()], nutrition=_reported_dinner_estimate())
    receipt = _service(db, provider).ingest_text(
        user_id, "Yesterday, for dinner, I ate 200 gram chicken, 3 rotis, some rice, and dal"
    )

    (ref,) = receipt.created
    assert ref.type == "meal"
    assert ref.nutrition_pending is False

    row = _fetch(db, user_id, ref.id)
    nutrition = row["payload"]["nutrition"]
    assert nutrition["protein_g"] == pytest.approx(84.6)  # 62 + 9.6 + 4 + 9, engine-summed
    assert nutrition["coverage"] == {"counted": 4, "excluded": 0}
    assert nutrition["estimated"] is True
    assert nutrition["confidence_class"] == "low"  # the dal is a dish with an assumed portion

    # The stated/assumed split survives into storage, per component.
    bases = {c["item"]: c["qty_basis"] for c in nutrition["components"]}
    assert bases == {
        "chicken": "stated", "rotis": "stated", "rice": "ai_estimated", "dal": "ai_estimated"
    }

    # And the question that failed now answers, from SQL alone.
    result = _aggregate(db, user_id)
    (bucket,) = result.buckets
    assert bucket.value == pytest.approx(84.6)
    assert bucket.n == 1
    assert bucket.n_estimated == 1  # the row is flagged estimated → narrator says "approximately"
    assert ref.id in bucket.evidence_ids  # ...and the number stays citable


def test_estimate_is_frozen_at_write_time_not_recomputed_on_read(db, user_id):
    """The determinism guarantee. The model is called once, during ingestion; every later
    aggregation is pure SQL over the stored value, however many times it runs."""
    provider = FakeModelProvider([_reported_dinner()], nutrition=_reported_dinner_estimate())
    _service(db, provider).ingest_text(user_id, "dinner")
    assert provider.nutrition_calls == 1

    first = _aggregate(db, user_id).buckets[0].value
    second = _aggregate(db, user_id).buckets[0].value

    assert first == second
    assert provider.nutrition_calls == 1  # reads never call the model


# ── an arbitrary dish: no local recipe database ────────────────────────────────────────
def test_arbitrary_dish_is_estimated_without_any_local_food_data(db, user_id):
    """Chicken Manchurian — a dish nothing in this repo has ever heard of.

    The proof that the system carries no food or recipe database: the engine contributes bounds
    checking and arithmetic, the model contributes the food knowledge, and a dish that exists in
    no local table still lands as a stored, aggregatable, explainable number."""
    event = ExtractedEvent(
        type="meal", event_time=DINNER, tz=TZ, confidence=0.6,
        summary="Chicken Manchurian",
        payload={"meal_type": "dinner", "items": [{"name": "chicken manchurian"}]},
    )
    estimate = [
        {
            "item": "chicken manchurian", "resolved": True, "kind": "dish",
            "understood_as": "Indo-Chinese fried chicken in a sweet-savoury sauce",
            "qty_g": 250, "qty_basis": "ai_estimated",
            "qty_note": "no quantity given — assumed one restaurant serving ≈ 250 g",
            "protein_g": 22.0, "carbs_g": 28.0, "fat_g": 19.0, "kcal": 371.0,
            "range": {"protein_g": [15.0, 30.0], "kcal": [280.0, 520.0]},
            "assumptions": ["deep-fried preparation", "restaurant-style sauce",
                            "preparation varies widely between kitchens"],
            "model_confidence": 0.45,
        }
    ]

    receipt = _service(db, FakeModelProvider([event], nutrition=estimate)).ingest_text(
        user_id, "I ate Chicken Manchurian"
    )

    nutrition = _fetch(db, user_id, receipt.created[0].id)["payload"]["nutrition"]
    assert nutrition["protein_g"] == 22.0
    assert nutrition["confidence_class"] == "low"  # assumed portion of a prepared dish
    component = nutrition["components"][0]
    assert component["range"]["protein_g"] == [15.0, 30.0]
    assert "deep-fried preparation" in component["assumptions"]
    assert "restaurant serving" in component["qty_note"]

    assert _aggregate(db, user_id).buckets[0].value == 22.0


def test_unresolvable_dish_is_excluded_from_the_total_and_named(db, user_id):
    """A meal of one unrecognised dish has no metric value at all, so it never reaches the
    aggregate. ``excluded_foods`` is what stops that absence from being invisible — a total
    that quietly omits food is wrong in the one direction the user cannot detect."""
    event = ExtractedEvent(
        type="meal", event_time=DINNER, tz=TZ, confidence=0.6,
        summary="grandma's special curry",
        payload={"meal_type": "dinner", "items": [{"name": "grandma's special curry"}]},
    )
    estimate = [
        {
            "item": "grandma's special curry", "resolved": False,
            "reason": "unrecognized_dish",
            "clarifying_question": "What's in it, and roughly how much did you have?",
        }
    ]

    receipt = _service(db, FakeModelProvider([event], nutrition=estimate)).ingest_text(
        user_id, "I ate grandma's special curry"
    )

    nutrition = _fetch(db, user_id, receipt.created[0].id)["payload"]["nutrition"]
    assert "protein_g" not in nutrition  # never a fabricated zero
    assert nutrition["unresolved"][0]["item"] == "grandma's special curry"

    result = _aggregate(db, user_id)
    assert result.is_empty  # nothing to sum...
    assert result.excluded_foods == ("grandma's special curry",)  # ...but the user is told why


def test_partially_unresolvable_meal_reports_both_the_total_and_the_gap(db, user_id):
    event = ExtractedEvent(
        type="meal", event_time=DINNER, tz=TZ, confidence=0.7,
        summary="200g chicken and grandma's special curry",
        payload={
            "meal_type": "dinner",
            "items": [{"name": "chicken", "qty_g": 200.0}, {"name": "grandma's curry"}],
        },
    )
    estimate = [
        _reported_dinner_estimate()[0],
        {"item": "grandma's curry", "resolved": False, "reason": "unrecognized_dish"},
    ]

    _service(db, FakeModelProvider([event], nutrition=estimate)).ingest_text(user_id, "dinner")

    result = _aggregate(db, user_id)
    assert result.buckets[0].value == 62.0  # the chicken alone
    assert result.excluded_foods == ("grandma's curry",)


# ── failure posture: a derived value never costs the user a fact ───────────────────────
def test_nutrition_failure_still_commits_the_meal(db, user_id):
    """Same posture as a failed embedding: the meal persists, the receipt says the estimate is
    pending, and the backfill is the recovery path. Losing a reported meal to a *derived*
    value's failure would invert never-lose-input."""
    provider = FakeModelProvider([_reported_dinner()], nutrition_error=True)
    receipt = _service(db, provider).ingest_text(user_id, "dinner")

    (ref,) = receipt.created
    assert receipt.parse_status == "ok"  # NOT a note fallback — the parse was fine
    assert ref.nutrition_pending is True

    row = _fetch(db, user_id, ref.id)
    assert row["type"] == "meal"
    assert "nutrition" not in row["payload"]
    assert row["payload"]["items"][0]["name"] == "chicken"  # the facts are all there


def test_meal_without_items_makes_no_estimate_call(db, user_id):
    """Nothing named is nothing to estimate — and spending a model call to learn that would be
    a cost with no possible result."""
    event = ExtractedEvent(
        type="meal", event_time=DINNER, tz=TZ, confidence=0.5,
        summary="had dinner", payload={"meal_type": "dinner"},
    )
    provider = FakeModelProvider([event], nutrition=_reported_dinner_estimate())
    _service(db, provider).ingest_text(user_id, "had dinner")

    assert provider.nutrition_calls == 0


def test_non_meal_events_never_reach_the_nutrition_stage(db, user_id):
    event = ExtractedEvent(
        type="weight", event_time=DINNER, tz=TZ, confidence=1.0,
        summary="75.2 kg", payload={"weight_kg": 75.2},
    )
    provider = FakeModelProvider([event], nutrition=_reported_dinner_estimate())
    _service(db, provider).ingest_text(user_id, "weighed 75.2 kg")

    assert provider.nutrition_calls == 0


# ── the no-overwrite rule ──────────────────────────────────────────────────────────────
def test_existing_nutrition_is_never_overwritten(db, user_id):
    """A payload that already carries nutrition this pipeline did not author — a value the user
    stated, or a reviewed replay-table macro set — is left exactly as it is, and no model call
    is even made."""
    event = ExtractedEvent(
        type="meal", event_time=DINNER, tz=TZ, confidence=0.9,
        summary="lunch: 200g paneer",
        payload={
            "meal_type": "lunch",
            "items": [{"name": "paneer", "qty_g": 200}],
            "nutrition": {"protein_g": 36.0, "kcal": 592.0, "estimated": True},
        },
    )
    provider = FakeModelProvider([event], nutrition=_reported_dinner_estimate())
    receipt = _service(db, provider).ingest_text(user_id, "200g paneer")

    assert provider.nutrition_calls == 0
    nutrition = _fetch(db, user_id, receipt.created[0].id)["payload"]["nutrition"]
    assert nutrition == {"protein_g": 36.0, "kcal": 592.0, "estimated": True}


def test_estimation_can_be_switched_off_for_bulk_paths(db, user_id):
    provider = FakeModelProvider([_reported_dinner()], nutrition=_reported_dinner_estimate())
    receipt = _service(db, provider, estimate_nutrition=False).ingest_text(user_id, "dinner")

    assert provider.nutrition_calls == 0
    assert "nutrition" not in _fetch(db, user_id, receipt.created[0].id)["payload"]


# ── backfill ───────────────────────────────────────────────────────────────────────────
def test_backfill_fills_missing_nutrition_and_is_idempotent(db, user_id):
    """The recovery path for meals logged before this stage existed, or while it was failing.

    Idempotence is the property that matters: a second sweep must do nothing, because the
    candidate query and the UPDATE both require the key to be absent."""
    provider = FakeModelProvider([_reported_dinner()], nutrition_error=True)
    svc = _service(db, provider)
    receipt = svc.ingest_text(user_id, "dinner")
    memory_id = receipt.created[0].id
    assert "nutrition" not in _fetch(db, user_id, memory_id)["payload"]

    provider.nutrition_error = False
    provider.nutrition = _reported_dinner_estimate()

    assert svc.backfill_nutrition(user_id) == 1
    payload = _fetch(db, user_id, memory_id)["payload"]
    assert payload["nutrition"]["protein_g"] == pytest.approx(84.6)
    assert payload["items"][0]["name"] == "chicken"  # facts untouched by the merge

    calls_after_first = provider.nutrition_calls
    assert svc.backfill_nutrition(user_id) == 0
    assert provider.nutrition_calls == calls_after_first  # no second estimate attempted


def test_backfill_never_touches_nutrition_it_did_not_author(db, user_id):
    event = ExtractedEvent(
        type="meal", event_time=DINNER, tz=TZ, confidence=0.9,
        summary="lunch: 200g paneer",
        payload={
            "meal_type": "lunch",
            "items": [{"name": "paneer", "qty_g": 200}],
            "nutrition": {"protein_g": 36.0, "estimated": True},
        },
    )
    svc = _service(db, FakeModelProvider([event], nutrition=_reported_dinner_estimate()))
    receipt = svc.ingest_text(user_id, "200g paneer")

    assert svc.backfill_nutrition(user_id) == 0
    assert _fetch(db, user_id, receipt.created[0].id)["payload"]["nutrition"]["protein_g"] == 36.0


def test_backfill_skips_meals_with_no_items(db, user_id):
    """Otherwise the drain loop would re-offer a row it can never fill, forever."""
    event = ExtractedEvent(
        type="meal", event_time=DINNER, tz=TZ, confidence=0.5,
        summary="had dinner", payload={"meal_type": "dinner"},
    )
    svc = _service(db, FakeModelProvider([event], nutrition=_reported_dinner_estimate()))
    svc.ingest_text(user_id, "had dinner")

    assert svc.backfill_nutrition(user_id) == 0
