"""``GET /api/today`` — the home screen's one read.

Transport only, like every other router here: authenticate, call ``engine.today.build_today``,
shape JSON. The intelligence, such as it is, lives in the engine; nothing is computed twice.

**One request, not six.** Today needs stats, targets, two days of two metrics, coverage, a
weight, an insight and a recent list. Six round trips over the cross-region link this system
actually runs on (app in ``us-east-1``, cluster in ``ap-south-1``) is the N+1 mistake wearing a
different costume, and it is the difference between a home screen that paints and one that
assembles itself in front of the user.

**No model is invoked.** The response carries numbers and rows; the sentence a user reads is
templated in the client from those numbers. That is not a loophole in ADR-12 — the rule bans
deriving *structured data from model output*, and here there is no model output to derive from.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from api.deps import get_current_user
from engine.db import Database
from engine.today import MetricDay, TodaySnapshot, build_today

router = APIRouter(prefix="/api", tags=["today"])


def _db(request: Request) -> Database:
    return request.app.state.db


def _metric_json(m: MetricDay) -> dict:
    """A metric total, with ``has_data`` carried explicitly.

    ``value`` stays ``null`` when nothing was logged rather than collapsing to ``0`` — the
    client renders "nothing logged yet", and a JSON ``0`` here would make that impossible to
    distinguish from a real, logged zero.
    """
    return {
        "metric": m.metric,
        "day": m.day.isoformat(),
        "value": m.value,
        "n": m.n,
        "n_estimated": m.n_estimated,
        "has_data": m.has_data,
        "evidence_ids": [str(i) for i in m.evidence_ids],
    }


def _memory_json(row: dict) -> dict:
    """The same shape ``glassbox._memory_json`` serves, so one ``MemoryRow`` schema on the
    client covers both. Duplicated rather than imported to keep the router boundary flat —
    if a third route needs it, promote it then."""
    return {
        "id": str(row["id"]),
        "event_time": row["event_time"].isoformat(),
        "tz": row["tz"],
        "type": row["type"],
        "source": row["source"],
        "provenance": row["provenance"],
        "confidence": row["confidence"],
        "status": row["status"],
        "superseded_by": str(row["superseded_by"]) if row["superseded_by"] else None,
        "summary": row["summary"],
        "payload": row["payload"],
        "created_at": row["created_at"].isoformat(),
    }


def _insight_json(row: dict | None) -> dict | None:
    """The newest active insight, flattened to what "what changed" renders.

    ``hypothesis`` is the engine's own stored sentence, not a re-narration — it was written
    when the claim was derived and is served verbatim. ``created_at`` is kept because it is the
    bi-temporal half that makes the claim interesting: *when the engine knew*, as distinct from
    the window it is about (ADR-13.10).
    """
    if row is None:
        return None
    payload = row["payload"] or {}
    return {
        "id": str(row["id"]),
        "hypothesis": payload.get("hypothesis"),
        "series_metric": payload.get("series_metric"),
        "series_kind": payload.get("series_kind"),
        "kind": payload.get("kind"),
        "pattern_strength": payload.get("pattern_strength"),
        "pre_value": payload.get("pre_value"),
        "post_value": payload.get("post_value"),
        "window_start": payload.get("window_start"),
        "window_end": payload.get("window_end"),
        "evidence_ids": [str(i) for i in (payload.get("evidence_ids") or [])],
        "evidence_count": payload.get("evidence_count"),
        "created_at": row["created_at"].isoformat(),
    }


def _snapshot_json(s: TodaySnapshot) -> dict:
    return {
        "day": s.day.isoformat(),
        "tz": s.tz,
        "generated_at": s.generated_at.isoformat(),
        "stats": {
            "memories": s.memories,
            "days": s.days,
            "insights": s.insights,
            "first_event": s.first_event.isoformat() if s.first_event else None,
        },
        "targets": {
            "protein_g": s.protein_target_g,
            "calorie_kcal": s.calorie_target_kcal,
            "are_custom": s.targets_are_custom,
            "basis": s.target_basis,
        },
        "today": {
            "protein_g": _metric_json(s.today_protein),
            "kcal": _metric_json(s.today_kcal),
        },
        "yesterday": {
            "protein_g": _metric_json(s.yesterday_protein),
            "kcal": _metric_json(s.yesterday_kcal),
        },
        "days_logged_last_7": s.days_logged_last_7,
        "latest_weight": (
            {
                "id": str(s.latest_weight["id"]),
                "weight_kg": s.latest_weight["weight_kg"],
                "event_time": s.latest_weight["event_time"].isoformat(),
            }
            if s.latest_weight
            else None
        ),
        "insight": _insight_json(s.insight),
        "recent": [_memory_json(r) for r in s.recent],
        # The glass box for a screen nobody asked a question on: these are the statements that
        # produced every figure above.
        "steps": [step.to_json() for step in s.steps],
    }


@router.get("/today")
def get_today(request: Request, user_id: UUID = Depends(get_current_user)) -> dict:
    """The home screen's data. A brand-new account gets a well-formed, all-empty response —
    zeros and nulls are the honest answer, not an error (ADR-13.4, same posture as
    ``GET /api/stats``)."""
    settings = request.app.state.settings
    with _db(request).transaction() as cur:
        snapshot = build_today(
            cur, user_id, now=datetime.now(timezone.utc), tz=settings.default_tz
        )
    return _snapshot_json(snapshot)
