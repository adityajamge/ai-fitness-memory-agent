"""User profile: the current-state cache + goal/target history (ADR-17).

Two things live here, deliberately together: the pure target calculator (no DB, no model
call — the same purity contract ``engine/nutrition.py`` and ``engine/insights.py``'s
``EffectScale`` already hold) and the ``user_profile`` read/write functions.

**Current weight is never read or written here.** It stays a first-class ``weight`` memory
(ADR-17.2, ``engine.repository.latest_weight``) — this module would otherwise become a second,
driftable source of truth for the same fact.

**History.** Every field in ``HISTORY_FIELDS`` writes a ``profile_change`` memory in the same
transaction as its ``user_profile`` row update (ADR-17.1), mirroring
``engine.turns.persist_turn``'s turn+trace atomicity. Identity fields (name, DOB, sex, height,
units) do not — none of them gates a historical comparison, so none of them needs a trail.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from engine.memory import Memory
from engine.repository import insert_memories
from engine.types import payload_to_json, validate_payload

#: Fields whose change writes a `profile_change` memory (ADR-17.1).
HISTORY_FIELDS: frozenset[str] = frozenset(
    {
        "primary_goal",
        "activity_level",
        "target_weight_kg",
        "dietary_preference",
        "allergies",
        "protein_target_g",
        "calorie_target_kcal",
    }
)

#: Every column ``apply_profile_update`` may write, in insert order. ``user_id`` and
#: ``updated_at`` are handled separately (identity + always-now, respectively).
_COLUMNS: tuple[str, ...] = (
    "display_name",
    "date_of_birth",
    "sex",
    "height_cm",
    "units",
    "primary_goal",
    "activity_level",
    "target_weight_kg",
    "dietary_preference",
    "allergies",
    "injuries",
    "protein_target_g",
    "calorie_target_kcal",
    "targets_are_custom",
    "onboarded_at",
)

# ── target calculator (Mifflin-St Jeor BMR x activity x goal) — product heuristics, not
# clinical thresholds, same posture as engine/insights.py's EffectScale calibration ────────
_ACTIVITY_MULTIPLIER: dict[str, float] = {
    "sedentary": 1.2,
    "light": 1.375,
    "moderate": 1.55,
    "very_active": 1.725,
    "athlete": 1.9,
}
_GOAL_CALORIE_ADJUST_KCAL: dict[str, float] = {
    "lose_fat": -500.0,
    "build_muscle": 300.0,
    "recomp": 0.0,
    "maintain": 0.0,
    "general_health": 0.0,
}
_GOAL_PROTEIN_PER_KG: dict[str, float] = {
    "lose_fat": 2.0,
    "build_muscle": 1.8,
    "recomp": 2.0,
    "maintain": 1.6,
    "general_health": 1.6,
}
_MIN_CALORIE_FLOOR_KCAL = 1200.0

#: The closed vocabularies the API validates against (same "reviewed, not a free string" posture
#: as ``engine.types.INSIGHT_KINDS``).
PRIMARY_GOALS: frozenset[str] = frozenset(_GOAL_CALORIE_ADJUST_KCAL)
ACTIVITY_LEVELS: frozenset[str] = frozenset(_ACTIVITY_MULTIPLIER)


@dataclass(frozen=True, slots=True)
class TargetSuggestion:
    """A computed nutrition target, always carrying its own basis (DESIGN.md §6.19). A number
    with no visible provenance is exactly what this system's mono-means-database-value
    convention, and §16.3's "labeled heuristic, never unexplained," exist to prevent."""

    protein_g: float
    calorie_kcal: float
    basis: str


@dataclass(frozen=True, slots=True)
class Profile:
    user_id: UUID
    display_name: str | None = None
    date_of_birth: date | None = None
    sex: str | None = None
    height_cm: float | None = None
    units: str = "metric"
    primary_goal: str | None = None
    activity_level: str | None = None
    target_weight_kg: float | None = None
    dietary_preference: str | None = None
    allergies: list[str] = field(default_factory=list)
    injuries: str | None = None
    protein_target_g: float | None = None
    calorie_target_kcal: float | None = None
    targets_are_custom: bool = False
    onboarded_at: datetime | None = None
    updated_at: datetime | None = None

    @property
    def has_onboarded(self) -> bool:
        return self.onboarded_at is not None


def get_profile(cur: psycopg.Cursor, user_id: UUID) -> Profile:
    """The user's current-state row, or an all-empty ``Profile`` if they have none yet.

    There is no row-creation step at signup (ADR-17.3): the first ``PATCH``/onboarding call
    creates it via ``apply_profile_update``'s UPSERT, and reading before that point is exactly
    as valid as reading any other not-yet-populated account state in this product.
    """
    cur.execute("SELECT * FROM user_profile WHERE user_id = %s", [user_id])
    row = cur.fetchone()
    if row is None:
        return Profile(user_id=user_id)
    return _from_row(row)


def _from_row(row: dict) -> Profile:
    return Profile(
        user_id=row["user_id"],
        display_name=row["display_name"],
        date_of_birth=row["date_of_birth"],
        sex=row["sex"],
        height_cm=row["height_cm"],
        units=row["units"] or "metric",
        primary_goal=row["primary_goal"],
        activity_level=row["activity_level"],
        target_weight_kg=row["target_weight_kg"],
        dietary_preference=row["dietary_preference"],
        allergies=list(row["allergies"] or []),
        injuries=row["injuries"],
        protein_target_g=row["protein_target_g"],
        calorie_target_kcal=row["calorie_target_kcal"],
        targets_are_custom=bool(row["targets_are_custom"]),
        onboarded_at=row["onboarded_at"],
        updated_at=row["updated_at"],
    )


def compute_targets(
    *,
    weight_kg: float | None,
    height_cm: float | None,
    date_of_birth: date | None,
    sex: str | None,
    activity_level: str | None,
    primary_goal: str | None,
    today: date | None = None,
) -> TargetSuggestion | None:
    """Mifflin-St Jeor BMR x activity multiplier x goal adjustment. Pure: no DB, no model
    call. Returns ``None`` when the inputs cannot support a computation — declining rather than
    guessing at a missing one, the same posture ``engine/nutrition.py`` holds toward an
    unrecognized food and ``engine/insights.py`` holds toward an ungated change.

    Sex is genuinely optional (DESIGN.md §6.19): when absent, the BMR uses the midpoint of the
    male/female offsets and the basis string says so explicitly — never silently assumed.
    """
    if (
        weight_kg is None
        or weight_kg <= 0
        or height_cm is None
        or height_cm <= 0
        or date_of_birth is None
        or activity_level not in _ACTIVITY_MULTIPLIER
    ):
        return None

    goal = primary_goal if primary_goal in _GOAL_CALORIE_ADJUST_KCAL else "maintain"
    age = _age_years(date_of_birth, today or datetime.now(timezone.utc).date())
    if age <= 0:
        return None

    if sex == "male":
        bmr = 10 * weight_kg + 6.25 * height_cm - 5 * age + 5
        sex_basis = "male"
    elif sex == "female":
        bmr = 10 * weight_kg + 6.25 * height_cm - 5 * age - 161
        sex_basis = "female"
    else:
        # Midpoint of the male (+5) / female (-161) Mifflin-St Jeor offsets — a sex-neutral
        # average, not a guess at which one applies.
        bmr = 10 * weight_kg + 6.25 * height_cm - 5 * age - 78
        sex_basis = "unspecified (sex-averaged)"

    multiplier = _ACTIVITY_MULTIPLIER[activity_level]
    tdee = bmr * multiplier
    adjust = _GOAL_CALORIE_ADJUST_KCAL[goal]
    calorie_kcal = max(_MIN_CALORIE_FLOOR_KCAL, tdee + adjust)
    protein_per_kg = _GOAL_PROTEIN_PER_KG[goal]
    protein_g = protein_per_kg * weight_kg

    basis = (
        f"Mifflin-St Jeor BMR ({sex_basis}, age {age}, {weight_kg:g}kg, {height_cm:g}cm) "
        f"x {activity_level} activity ({multiplier:g}x) = {tdee:.0f} kcal TDEE; "
        f"{goal.replace('_', ' ')} adjustment {adjust:+.0f} kcal; "
        f"protein at {protein_per_kg:g} g/kg bodyweight"
    )
    return TargetSuggestion(
        protein_g=round(protein_g, 1),
        calorie_kcal=round(calorie_kcal),
        basis=basis,
    )


def _age_years(dob: date, today: date) -> int:
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


def apply_profile_update(
    cur: psycopg.Cursor,
    user_id: UUID,
    updates: dict[str, Any],
    *,
    now: datetime | None = None,
    tz: str = "UTC",
    source: str = "profile",
) -> Profile:
    """Merge ``updates`` onto the current profile, UPSERT the row, and write a
    ``profile_change`` memory for every changed ``HISTORY_FIELDS`` entry — all inside the
    caller's transaction, so the row and its history are atomic with each other (ADR-17.1).

    ``updates`` keys outside ``_COLUMNS`` are ignored rather than rejected, so a caller can
    pass a validated request body straight through without a second allowlist. Current weight
    is never a key here (ADR-17.2) — callers write it as a `weight` memory separately.
    """
    now = now or datetime.now(timezone.utc)
    current = get_profile(cur, user_id)
    merged: dict[str, Any] = {col: getattr(current, col) for col in _COLUMNS}

    changes: list[tuple[str, Any, Any]] = []
    for key, value in updates.items():
        if key not in _COLUMNS:
            continue
        old = merged[key]
        if value == old:
            continue
        merged[key] = value
        if key in HISTORY_FIELDS:
            changes.append((key, old, value))

    _upsert_row(cur, user_id, merged, now)
    if changes:
        _write_history(cur, user_id, changes, now=now, tz=tz, source=source)
    return get_profile(cur, user_id)


def _upsert_row(cur: psycopg.Cursor, user_id: UUID, merged: dict[str, Any], now: datetime) -> None:
    columns = ("user_id", *_COLUMNS, "updated_at")
    values = [user_id, *(_to_sql(merged[c]) for c in _COLUMNS), now]
    placeholders = ", ".join(["%s"] * len(columns))
    # Every name here comes from the static _COLUMNS tuple above, never from a request body —
    # safe to interpolate (same posture as repository.py's insert_memory emb_sql/emb_params).
    update_clause = ", ".join(f"{c} = EXCLUDED.{c}" for c in (*_COLUMNS, "updated_at"))
    cur.execute(
        f"""
        INSERT INTO user_profile ({", ".join(columns)})
        VALUES ({placeholders})
        ON CONFLICT (user_id) DO UPDATE SET {update_clause}
        """,
        values,
    )


def _to_sql(value: Any) -> Any:
    """``allergies`` is the one list-shaped column; everything else passes through untouched."""
    return Jsonb(value) if isinstance(value, list) else value


def _write_history(
    cur: psycopg.Cursor,
    user_id: UUID,
    changes: list[tuple[str, Any, Any]],
    *,
    now: datetime,
    tz: str,
    source: str,
) -> None:
    memories = []
    for field_name, old_value, new_value in changes:
        payload = validate_payload(
            "profile_change",
            {
                "field": field_name,
                "old_value": _jsonable(old_value),
                "new_value": _jsonable(new_value),
            },
        )
        memories.append(
            Memory(
                user_id=user_id,
                event_time=now,
                tz=tz,
                type="profile_change",
                source=source,
                provenance="live",
                confidence=1.0,
                summary=_change_summary(field_name, old_value, new_value),
                payload=payload_to_json(payload),
            )
        )
    insert_memories(cur, memories)


def _jsonable(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def _change_summary(field_name: str, old_value: Any, new_value: Any) -> str:
    label = field_name.replace("_", " ")
    if old_value is None:
        return f"{label} set to {new_value}"
    return f"{label} changed from {old_value} to {new_value}"


def render_profile_note(profile: Profile, weight_row: dict | None) -> str | None:
    """One line of prose context for the narrator (ADR-17.5) — attached to ``ContextBlock``
    by the graph, never computed inside ``assemble()`` (which stays DB-free by construction).
    Returns ``None`` when the profile carries nothing worth mentioning, so a brand-new account
    (or one that skipped onboarding) narrates exactly as it did before this feature existed.
    """
    parts: list[str] = []
    if profile.display_name:
        parts.append(profile.display_name)
    if profile.primary_goal:
        parts.append(f"goal: {profile.primary_goal.replace('_', ' ')}")
    if profile.protein_target_g or profile.calorie_target_kcal:
        target_bits = []
        if profile.protein_target_g:
            target_bits.append(f"{profile.protein_target_g:g}g protein/day")
        if profile.calorie_target_kcal:
            target_bits.append(f"{profile.calorie_target_kcal:g} kcal/day")
        parts.append(f"targets: {', '.join(target_bits)}")
    if profile.dietary_preference:
        parts.append(f"diet: {profile.dietary_preference}")
    if profile.allergies:
        parts.append(f"allergies: {', '.join(profile.allergies)}")
    if weight_row is not None:
        parts.append(f"last known weight: {weight_row['weight_kg']:g}kg")
    if not parts:
        return None
    return "; ".join(parts)
