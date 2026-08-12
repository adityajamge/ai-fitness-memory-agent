"""``engine/profile.py`` — the target calculator (pure) and the current-state + history
write path (real CockroachDB via the ``db``/``user_id`` fixtures).

The organizing questions: does the calculator ever guess rather than decline (ADR-17.4), and
does every history-worthy field change leave a `profile_change` memory while identity fields
and no-op writes leave none (ADR-17.1)?
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from uuid import UUID, uuid4

import pytest

from engine.profile import (
    HISTORY_FIELDS,
    apply_profile_update,
    compute_targets,
    get_profile,
)
from engine.tests.dbcleanup import register_user

_TODAY = date(2026, 8, 11)


@pytest.fixture()
def user_id(db) -> UUID:
    """Overrides ``engine/tests/conftest.py``'s ``user_id`` fixture. ``user_profile`` has a
    real foreign key to ``users`` (unlike ``memories``, which has none) — every profile test
    needs an actual row there, not just a registered-but-nonexistent id."""
    new_id = uuid4()
    with db.transaction() as cur:
        cur.execute(
            "INSERT INTO users (id, email, password_hash, salt) VALUES (%s, %s, %s, %s)",
            [new_id, f"profile-test-{new_id.hex}@example.com", b"x", b"y"],
        )
    return register_user(new_id)


def _base_inputs(**overrides) -> dict:
    base = dict(
        weight_kg=80.0,
        height_cm=178.0,
        date_of_birth=date(1997, 3, 15),  # 29 on _TODAY
        sex="male",
        activity_level="moderate",
        primary_goal="lose_fat",
        today=_TODAY,
    )
    base.update(overrides)
    return base


# ── compute_targets: pure, no DB, no model call ─────────────────────────────────────────
def test_missing_weight_declines_rather_than_guessing() -> None:
    assert compute_targets(**_base_inputs(weight_kg=None)) is None


def test_missing_height_declines() -> None:
    assert compute_targets(**_base_inputs(height_cm=None)) is None


def test_missing_date_of_birth_declines() -> None:
    assert compute_targets(**_base_inputs(date_of_birth=None)) is None


def test_unknown_activity_level_declines() -> None:
    assert compute_targets(**_base_inputs(activity_level="marathon-training")) is None


def test_male_basis_matches_mifflin_st_jeor_by_hand() -> None:
    result = compute_targets(**_base_inputs())
    assert result is not None
    bmr = 10 * 80.0 + 6.25 * 178.0 - 5 * 29 + 5
    tdee = bmr * 1.55  # moderate
    expected_kcal = round(tdee - 500)  # lose_fat
    assert result.calorie_kcal == expected_kcal
    assert result.protein_g == round(2.0 * 80.0, 1)  # lose_fat: 2.0 g/kg
    assert "male" in result.basis
    assert "moderate" in result.basis


def test_female_basis_differs_from_male_by_the_offset() -> None:
    male = compute_targets(**_base_inputs(sex="male"))
    female = compute_targets(**_base_inputs(sex="female"))
    assert male is not None and female is not None
    # Same everything except the +5 / -161 Mifflin-St Jeor offset — 166 kcal of BMR, scaled by
    # the moderate-activity multiplier (1.55x) once it reaches calorie_kcal.
    assert male.calorie_kcal - female.calorie_kcal == pytest.approx(166 * 1.55, abs=1)


def test_unspecified_sex_is_averaged_and_says_so_never_silently_assumed() -> None:
    male = compute_targets(**_base_inputs(sex="male"))
    female = compute_targets(**_base_inputs(sex="female"))
    unspecified = compute_targets(**_base_inputs(sex=None))
    assert male is not None and female is not None and unspecified is not None
    assert "sex-averaged" in unspecified.basis
    assert female.calorie_kcal < unspecified.calorie_kcal < male.calorie_kcal


def test_unknown_goal_falls_back_to_maintain_not_a_guessed_one() -> None:
    unknown_goal = compute_targets(**_base_inputs(primary_goal="get-shredded-fast"))
    maintain = compute_targets(**_base_inputs(primary_goal="maintain"))
    assert unknown_goal is not None and maintain is not None
    assert unknown_goal.calorie_kcal == maintain.calorie_kcal
    assert unknown_goal.protein_g == maintain.protein_g


def test_calorie_floor_is_never_crossed_even_for_a_light_low_activity_deficit() -> None:
    result = compute_targets(
        **_base_inputs(weight_kg=45.0, height_cm=150.0, activity_level="sedentary")
    )
    assert result is not None
    assert result.calorie_kcal >= 1200.0


def test_every_suggestion_carries_a_nonempty_basis() -> None:
    result = compute_targets(**_base_inputs())
    assert result is not None
    assert result.basis.strip() != ""


# ── get_profile / apply_profile_update: real DB ─────────────────────────────────────────
def _history_rows(db, user_id) -> list[dict]:
    # Ordered by event_time (caller-controlled via apply_profile_update's `now=`), not
    # created_at — two writes in the same transaction can share CockroachDB's transaction
    # timestamp, so created_at cannot disambiguate write order within one test.
    with db.transaction() as cur:
        cur.execute(
            "SELECT payload, source, event_time FROM memories "
            "WHERE user_id = %s AND type = 'profile_change' ORDER BY event_time, id",
            [user_id],
        )
        return cur.fetchall()


def test_fresh_user_has_an_empty_unonboarded_profile(db, user_id):
    with db.transaction() as cur:
        profile = get_profile(cur, user_id)
    assert profile.user_id == user_id
    assert profile.display_name is None
    assert profile.has_onboarded is False
    assert profile.allergies == []


def test_identity_field_update_writes_no_history(db, user_id):
    with db.transaction() as cur:
        apply_profile_update(cur, user_id, {"display_name": "Aditya", "sex": "male"})
    assert _history_rows(db, user_id) == []
    with db.transaction() as cur:
        profile = get_profile(cur, user_id)
    assert profile.display_name == "Aditya"
    assert profile.sex == "male"


def test_history_field_first_set_writes_one_profile_change_with_old_value_none(db, user_id):
    with db.transaction() as cur:
        apply_profile_update(cur, user_id, {"primary_goal": "lose_fat"})
    rows = _history_rows(db, user_id)
    assert len(rows) == 1
    assert rows[0]["payload"]["field"] == "primary_goal"
    # payload_to_json drops None hot fields (engine/types.py) — absent, not null, for a
    # field's first-ever value.
    assert rows[0]["payload"].get("old_value") is None
    assert rows[0]["payload"]["new_value"] == "lose_fat"
    assert rows[0]["source"] == "profile"  # apply_profile_update's default


def test_history_field_changed_again_records_the_prior_value(db, user_id):
    first = datetime(2026, 8, 1, tzinfo=timezone.utc)
    second = datetime(2026, 8, 2, tzinfo=timezone.utc)
    with db.transaction() as cur:
        apply_profile_update(cur, user_id, {"primary_goal": "lose_fat"}, now=first)
        apply_profile_update(
            cur, user_id, {"primary_goal": "build_muscle"}, now=second, source="onboarding"
        )
    rows = _history_rows(db, user_id)
    assert len(rows) == 2
    assert rows[1]["payload"]["old_value"] == "lose_fat"
    assert rows[1]["payload"]["new_value"] == "build_muscle"
    assert rows[1]["source"] == "onboarding"


def test_setting_the_same_value_again_is_a_true_no_op(db, user_id):
    with db.transaction() as cur:
        apply_profile_update(cur, user_id, {"activity_level": "moderate"})
        apply_profile_update(cur, user_id, {"activity_level": "moderate"})
    assert len(_history_rows(db, user_id)) == 1


def test_allergies_list_round_trips_through_jsonb(db, user_id):
    with db.transaction() as cur:
        apply_profile_update(cur, user_id, {"allergies": ["peanuts", "shellfish"]})
        profile = get_profile(cur, user_id)
    assert profile.allergies == ["peanuts", "shellfish"]


def test_unknown_update_keys_are_ignored_not_rejected(db, user_id):
    """``weight_kg`` is deliberately not a ``user_profile`` column (ADR-17.2) — passing it
    through ``apply_profile_update`` must be a harmless no-op, never an error."""
    with db.transaction() as cur:
        profile = apply_profile_update(cur, user_id, {"weight_kg": 82.0, "display_name": "Priya"})
    assert profile.display_name == "Priya"
    assert not hasattr(profile, "weight_kg")


_PROBE_VALUES: dict[str, object] = {
    "primary_goal": "lose_fat",
    "activity_level": "moderate",
    "target_weight_kg": 70.0,
    "dietary_preference": "vegetarian",
    "allergies": ["peanuts"],
    "protein_target_g": 140.0,
    "calorie_target_kcal": 2100.0,
}


def test_every_history_field_name_is_a_real_column(db, user_id):
    """Guards HISTORY_FIELDS against drifting from the schema it annotates — every entry must
    both be a real column (the UPSERT would error otherwise) and actually be tracked
    (asserted per-field so a silently-dropped key can't hide behind the others)."""
    assert set(_PROBE_VALUES) == HISTORY_FIELDS
    with db.transaction() as cur:
        for name, value in _PROBE_VALUES.items():
            apply_profile_update(cur, user_id, {name: value})
    rows = _history_rows(db, user_id)
    assert {row["payload"]["field"] for row in rows} == HISTORY_FIELDS
