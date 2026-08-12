"""Profile routes: the onboarding intake and the profile/goals read+edit surface (ADR-17).

Two write entry points, one shared shape. ``POST /api/profile/onboarding`` is the one-time
intake (DESIGN.md §6.19) — it also accepts ``skipped`` and always marks ``onboarded_at``.
``PATCH /api/profile`` is every edit after that, from Profile settings. Both funnel through
``engine.profile.apply_profile_update`` for the ``user_profile`` row + `profile_change` history,
and both write **current weight** as a `weight` memory (ADR-17.2), never a profile column —
`weight_kg` in the request body is a pseudo-field, deliberately excluded from the columns
`apply_profile_update` ever sees.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, field_validator

from api.deps import get_current_user
from engine.db import Database
from engine.ingestion import IngestionService
from engine.model import ExtractedEvent
from engine.profile import (
    ACTIVITY_LEVELS,
    PRIMARY_GOALS,
    Profile,
    TargetSuggestion,
    apply_profile_update,
    compute_targets,
    get_profile,
)
from engine.repository import latest_weight

router = APIRouter(prefix="/api/profile", tags=["profile"])

_UNITS = frozenset({"metric", "imperial"})
_SEX = frozenset({"male", "female"})


class ProfileEdit(BaseModel):
    """A partial edit. Every field is optional and only the ones actually present in the
    request body are applied (``model_fields_set``, in ``_edit_updates`` below) — the real
    partial-update semantics a PATCH promises, not a full-replace with implicit nulling."""

    display_name: str | None = None
    date_of_birth: date | None = None
    sex: str | None = None
    height_cm: float | None = None
    units: str | None = None
    primary_goal: str | None = None
    activity_level: str | None = None
    target_weight_kg: float | None = None
    dietary_preference: str | None = None
    allergies: list[str] | None = None
    injuries: str | None = None
    protein_target_g: float | None = None
    calorie_target_kcal: float | None = None
    #: Not a `user_profile` column (ADR-17.2). Present writes a new `weight` memory — the same
    #: path a chat-logged weight takes — and never touches the profile row directly.
    weight_kg: float | None = None

    @field_validator("sex")
    @classmethod
    def _valid_sex(cls, v: str | None) -> str | None:
        if v is not None and v not in _SEX:
            raise ValueError(f"sex must be one of {sorted(_SEX)}")
        return v

    @field_validator("units")
    @classmethod
    def _valid_units(cls, v: str | None) -> str | None:
        if v is not None and v not in _UNITS:
            raise ValueError(f"units must be one of {sorted(_UNITS)}")
        return v

    @field_validator("primary_goal")
    @classmethod
    def _valid_goal(cls, v: str | None) -> str | None:
        if v is not None and v not in PRIMARY_GOALS:
            raise ValueError(f"primary_goal must be one of {sorted(PRIMARY_GOALS)}")
        return v

    @field_validator("activity_level")
    @classmethod
    def _valid_activity(cls, v: str | None) -> str | None:
        if v is not None and v not in ACTIVITY_LEVELS:
            raise ValueError(f"activity_level must be one of {sorted(ACTIVITY_LEVELS)}")
        return v


class OnboardingSubmit(ProfileEdit):
    """The intake screen's submission. ``skipped`` is the "Skip for now" escape hatch
    (DESIGN.md §6.19) — it still records whatever fields were filled in before the skip; it
    only means the required-fields gate was not enforced client-side."""

    skipped: bool = False


def _edit_updates(body: ProfileEdit) -> dict[str, Any]:
    """The client-sent fields, minus the pseudo-fields that are never `user_profile` columns.
    Setting a target explicitly (rather than letting it stay engine-computed) is what
    "custom" means here, so it flips the flag in the same update."""
    data = body.model_dump(exclude_unset=True, exclude={"weight_kg", "skipped"})
    if "protein_target_g" in data or "calorie_target_kcal" in data:
        data["targets_are_custom"] = True
    return data


def _log_weight(
    ingestion: IngestionService,
    user_id: UUID,
    weight_kg: float,
    now: datetime,
    tz: str,
    *,
    source: str,
) -> None:
    ingestion.ingest_events(
        user_id,
        [
            ExtractedEvent(
                type="weight",
                event_time=now,
                tz=tz,
                confidence=1.0,
                summary=f"{weight_kg:g}kg",
                payload={"weight_kg": weight_kg},
            )
        ],
        source=source,
        provenance="live",
    )


def _suggestion_for(profile: Profile, current_weight_kg: float | None) -> TargetSuggestion | None:
    return compute_targets(
        weight_kg=current_weight_kg,
        height_cm=profile.height_cm,
        date_of_birth=profile.date_of_birth,
        sex=profile.sex,
        activity_level=profile.activity_level,
        primary_goal=profile.primary_goal,
    )


def _profile_json(
    profile: Profile, weight_row: dict | None, suggested: TargetSuggestion | None
) -> dict:
    return {
        "display_name": profile.display_name,
        "date_of_birth": profile.date_of_birth.isoformat() if profile.date_of_birth else None,
        "sex": profile.sex,
        "height_cm": profile.height_cm,
        "units": profile.units,
        "primary_goal": profile.primary_goal,
        "activity_level": profile.activity_level,
        "target_weight_kg": profile.target_weight_kg,
        "dietary_preference": profile.dietary_preference,
        "allergies": profile.allergies,
        "injuries": profile.injuries,
        "protein_target_g": profile.protein_target_g,
        "calorie_target_kcal": profile.calorie_target_kcal,
        "targets_are_custom": profile.targets_are_custom,
        "current_weight_kg": weight_row["weight_kg"] if weight_row else None,
        "current_weight_at": weight_row["event_time"].isoformat() if weight_row else None,
        "suggested_targets": (
            {
                "protein_g": suggested.protein_g,
                "calorie_kcal": suggested.calorie_kcal,
                "basis": suggested.basis,
            }
            if suggested
            else None
        ),
        "onboarded_at": profile.onboarded_at.isoformat() if profile.onboarded_at else None,
        "has_onboarded": profile.has_onboarded,
    }


def _refresh_computed_targets(
    cur, user_id: UUID, profile: Profile, *, now: datetime, tz: str
) -> Profile:
    """When targets are not a manual override, recompute them from the just-applied profile
    state and write the result — itself a `profile_change` if it moved (ADR-17.1). Keeps the
    stored target current with weight/goal/activity edits without ever touching it once the
    user has taken it over (`targets_are_custom`)."""
    if profile.targets_are_custom:
        return profile
    weight_row = latest_weight(cur, user_id)
    suggested = _suggestion_for(profile, weight_row["weight_kg"] if weight_row else None)
    if suggested is None:
        return profile
    if (
        profile.protein_target_g == suggested.protein_g
        and profile.calorie_target_kcal == suggested.calorie_kcal
    ):
        return profile
    return apply_profile_update(
        cur,
        user_id,
        {"protein_target_g": suggested.protein_g, "calorie_target_kcal": suggested.calorie_kcal},
        now=now,
        tz=tz,
        source="profile",
    )


@router.get("")
def read_profile(request: Request, user_id: UUID = Depends(get_current_user)) -> dict:
    db: Database = request.app.state.db
    with db.transaction() as cur:
        profile = get_profile(cur, user_id)
        weight_row = latest_weight(cur, user_id)
    suggested = _suggestion_for(profile, weight_row["weight_kg"] if weight_row else None)
    return _profile_json(profile, weight_row, suggested)


@router.patch("")
def edit_profile(
    body: ProfileEdit, request: Request, user_id: UUID = Depends(get_current_user)
) -> dict:
    db: Database = request.app.state.db
    ingestion: IngestionService = request.app.state.ingestion
    tz = request.app.state.settings.default_tz
    now = datetime.now(timezone.utc)

    if body.weight_kg is not None:
        _log_weight(ingestion, user_id, body.weight_kg, now, tz, source="profile")

    with db.transaction() as cur:
        profile = apply_profile_update(
            cur, user_id, _edit_updates(body), now=now, tz=tz, source="profile"
        )
        profile = _refresh_computed_targets(cur, user_id, profile, now=now, tz=tz)
        weight_row = latest_weight(cur, user_id)
    suggested = _suggestion_for(profile, weight_row["weight_kg"] if weight_row else None)
    return _profile_json(profile, weight_row, suggested)


@router.post("/onboarding")
def submit_onboarding(
    body: OnboardingSubmit, request: Request, user_id: UUID = Depends(get_current_user)
) -> dict:
    db: Database = request.app.state.db
    ingestion: IngestionService = request.app.state.ingestion
    tz = request.app.state.settings.default_tz
    now = datetime.now(timezone.utc)

    if body.weight_kg is not None:
        _log_weight(ingestion, user_id, body.weight_kg, now, tz, source="onboarding")

    updates = _edit_updates(body)
    updates["onboarded_at"] = now
    with db.transaction() as cur:
        profile = apply_profile_update(cur, user_id, updates, now=now, tz=tz, source="onboarding")
        profile = _refresh_computed_targets(cur, user_id, profile, now=now, tz=tz)
        weight_row = latest_weight(cur, user_id)
    suggested = _suggestion_for(profile, weight_row["weight_kg"] if weight_row else None)
    return _profile_json(profile, weight_row, suggested)
