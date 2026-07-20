"""T3 drift canary: the payload registry accepts unknown keys and types the hot fields.

These are the guarantees the migration-free JSONB design rests on (04 design rule,
ADR-13.6). No database needed — pure validation.
"""

import pytest
from pydantic import ValidationError

from engine.types import (
    MealPayload,
    UnknownMemoryType,
    payload_to_json,
    validate_payload,
)


def test_unknown_keys_are_preserved():
    """A new nutrient tomorrow must survive validation without a migration."""
    payload = {
        "meal_type": "lunch",
        "items": [{"name": "curd", "qty_g": 250}],
        "nutrition": {"protein_g": 46, "kcal": 610, "creatine_g": 5},  # creatine_g is novel
    }
    meal = validate_payload("meal", payload)
    assert isinstance(meal, MealPayload)
    dumped = payload_to_json(meal)
    # extra key survives round-trip, nested under the value object that received it
    assert dumped["nutrition"]["creatine_g"] == 5
    assert dumped["nutrition"]["protein_g"] == 46


def test_hot_fields_are_typed():
    """Known hot fields are coerced to their declared type (string number -> float)."""
    meal = validate_payload("meal", {"nutrition": {"protein_g": "46"}})
    assert meal.nutrition is not None
    assert meal.nutrition.protein_g == 46.0
    assert isinstance(meal.nutrition.protein_g, float)


def test_hot_field_wrong_type_raises():
    """A hot field that can't coerce is a validation failure (routes turn -> note)."""
    with pytest.raises(ValidationError):
        validate_payload("body_scan", {"body_fat_pct": "not-a-number"})


def test_unknown_type_raises():
    with pytest.raises(UnknownMemoryType):
        validate_payload("horoscope", {"sign": "leo"})


def test_note_requires_text():
    """The note payload must carry the raw text — that's how never-lose-input is honored."""
    with pytest.raises(ValidationError):
        validate_payload("note", {})
    note = validate_payload("note", {"text": "250g curd, 3 eggs"})
    assert note.text == "250g curd, 3 eggs"


def test_payload_to_json_drops_none_keeps_falsey():
    """exclude_none drops empty hot fields but preserves explicit False/0 and extras."""
    meal = validate_payload(
        "meal", {"meal_type": "dinner", "nutrition": {"estimated": False, "kcal": 0}}
    )
    dumped = payload_to_json(meal)
    assert dumped["nutrition"]["estimated"] is False
    assert dumped["nutrition"]["kcal"] == 0
    assert "photo_s3_key" not in dumped  # None hot field dropped
