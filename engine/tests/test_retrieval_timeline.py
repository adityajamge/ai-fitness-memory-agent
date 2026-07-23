"""Timeline + lookup builder tests (M2 — 12-test-plan.md `engine/retrieval` timeline
block: ordered slice, range edges; plus the point-lookup family: last/first event,
item containment (inverted-index path), type-level counts)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from engine.db import Database
from engine.memory import Memory
from engine.repository import insert_memories, mark_superseded
from engine.retrieval import (
    CountSpec,
    LookupSpec,
    RetrievalSpecError,
    TimelineSpec,
    count_events,
    get_timeline,
    lookup_events,
)
from engine.trace import EvidenceSnapshot

UTC = timezone.utc
T0 = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
START = datetime(2026, 7, 1, tzinfo=UTC)
END = datetime(2026, 8, 1, tzinfo=UTC)


def _mem(
    user_id: UUID,
    event_time: datetime,
    type_: str = "meal",
    payload: dict | None = None,
    summary: str | None = None,
) -> Memory:
    return Memory(
        user_id=user_id,
        event_time=event_time,
        tz="Asia/Kolkata",
        type=type_,
        source="chat",
        provenance="live",
        confidence=0.9,
        payload=payload if payload is not None else {"meal_type": "lunch"},
        summary=summary,
    )


def _seed(db: Database, memories: list[Memory]) -> list[UUID]:
    with db.transaction() as cur:
        return insert_memories(cur, memories)


def _timeline(db: Database, user_id: UUID, spec: TimelineSpec):
    with db.transaction() as cur:
        return get_timeline(cur, user_id, spec)


def _lookup(db: Database, user_id: UUID, spec: LookupSpec):
    with db.transaction() as cur:
        return lookup_events(cur, user_id, spec)


def _count(db: Database, user_id: UUID, spec: CountSpec):
    with db.transaction() as cur:
        return count_events(cur, user_id, spec)


# ── timeline ──────────────────────────────────────────────────────────────────────────
def test_slice_is_ordered_by_event_time_despite_insert_order(db, user_id) -> None:
    times = [T0 + timedelta(days=2), T0, T0 + timedelta(days=1)]
    _seed(db, [_mem(user_id, t) for t in times])

    result, step = _timeline(db, user_id, TimelineSpec(start=START, end=END))

    assert [e.event_time for e in result.entries] == sorted(times)
    assert all(isinstance(e, EvidenceSnapshot) for e in result.entries)
    assert step.row_count == 3


def test_range_edges_are_half_open(db, user_id) -> None:
    _seed(db, [_mem(user_id, START), _mem(user_id, END)])
    result, _ = _timeline(db, user_id, TimelineSpec(start=START, end=END))
    assert [e.event_time for e in result.entries] == [START]


def test_types_filter(db, user_id) -> None:
    _seed(
        db,
        [
            _mem(user_id, T0, "meal"),
            _mem(user_id, T0 + timedelta(hours=1), "workout", {"activity": "squats"}),
            _mem(user_id, T0 + timedelta(hours=2), "sleep", {"hours": 7.5}),
        ],
    )
    spec = TimelineSpec(start=START, end=END, types=("meal", "sleep"))
    result, _ = _timeline(db, user_id, spec)
    assert [e.type for e in result.entries] == ["meal", "sleep"]


def test_limit_truncates_from_the_start_of_the_slice(db, user_id) -> None:
    _seed(db, [_mem(user_id, T0 + timedelta(hours=i)) for i in range(5)])
    result, _ = _timeline(db, user_id, TimelineSpec(start=START, end=END, limit=3))
    assert len(result.entries) == 3
    assert result.entries[0].event_time == T0  # earliest first, then cut


def test_superseded_and_other_users_rows_excluded(db, user_id) -> None:
    other = uuid4()
    _seed(db, [_mem(other, T0)])
    keep, drop = _seed(db, [_mem(user_id, T0), _mem(user_id, T0 + timedelta(hours=1))])
    with db.transaction() as cur:
        mark_superseded(cur, user_id, drop, superseded_by=keep)

    result, _ = _timeline(db, user_id, TimelineSpec(start=START, end=END))
    assert [e.id for e in result.entries] == [keep]


def test_empty_slice_is_defined(db, user_id) -> None:
    result, step = _timeline(db, user_id, TimelineSpec(start=START, end=END))
    assert result.is_empty
    assert result.entries == ()
    assert step.row_count == 0


@pytest.mark.parametrize(
    "kwargs",
    [
        {"start": END, "end": START},
        {"start": datetime(2026, 7, 1), "end": datetime(2026, 8, 1)},  # naive
        {"start": START, "end": END, "types": ()},
        {"start": START, "end": END, "types": ("diary",)},
        {"start": START, "end": END, "limit": 0},
        {"start": START, "end": END, "limit": 1001},
    ],
)
def test_invalid_timeline_slots_raise(kwargs) -> None:
    with pytest.raises(RetrievalSpecError):
        TimelineSpec(**kwargs)


# ── lookup: last/first + item containment ─────────────────────────────────────────────
def _chicken_meals(user_id: UUID) -> list[Memory]:
    return [
        _mem(
            user_id,
            T0,
            "meal",
            {"items": [{"name": "chicken", "qty_g": 200}], "meal_type": "lunch"},
            summary="lunch: 200g chicken",
        ),
        _mem(
            user_id,
            T0 + timedelta(days=3),
            "meal",
            {"items": [{"name": "paneer", "qty_g": 150}], "meal_type": "dinner"},
            summary="dinner: 150g paneer",
        ),
        _mem(
            user_id,
            T0 + timedelta(days=5),
            "meal",
            {"items": [{"name": "chicken", "qty_g": 250}], "meal_type": "dinner"},
            summary="dinner: 250g chicken",
        ),
    ]


def test_last_event_of_a_type(db, user_id) -> None:
    ids = _seed(db, _chicken_meals(user_id))
    result, step = _lookup(db, user_id, LookupSpec(type="meal"))
    assert [e.id for e in result.entries] == [ids[2]]  # newest
    assert step.family == "lookup"


def test_first_event_of_a_type(db, user_id) -> None:
    ids = _seed(db, _chicken_meals(user_id))
    result, _ = _lookup(db, user_id, LookupSpec(type="meal", direction="first"))
    assert [e.id for e in result.entries] == [ids[0]]  # oldest


def test_item_containment_answers_when_did_i_last_eat_chicken(db, user_id) -> None:
    ids = _seed(db, _chicken_meals(user_id))
    result, step = _lookup(db, user_id, LookupSpec(type="meal", item="chicken", n=2))
    # Both chicken meals, newest first; the paneer dinner (newer than the first chicken
    # meal) never matches.
    assert [e.id for e in result.entries] == [ids[2], ids[0]]
    assert step.params["contains"] == {"items": [{"name": "chicken"}]}


def test_item_with_no_match_is_empty_not_an_error(db, user_id) -> None:
    _seed(db, _chicken_meals(user_id))
    result, step = _lookup(db, user_id, LookupSpec(type="meal", item="tofu"))
    assert result.is_empty
    assert step.row_count == 0


def test_lookup_excludes_superseded_and_other_users(db, user_id) -> None:
    other = uuid4()
    _seed(db, [_mem(other, T0 + timedelta(days=30))])
    keep, drop = _seed(
        db, [_mem(user_id, T0), _mem(user_id, T0 + timedelta(days=1))]
    )
    with db.transaction() as cur:
        mark_superseded(cur, user_id, drop, superseded_by=keep)
    result, _ = _lookup(db, user_id, LookupSpec(type="meal"))
    assert [e.id for e in result.entries] == [keep]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"type": "diary"},
        {"type": "meal", "direction": "latest"},
        {"type": "meal", "item": "   "},
        {"type": "meal", "n": 0},
        {"type": "meal", "n": 21},
    ],
)
def test_invalid_lookup_slots_raise(kwargs) -> None:
    with pytest.raises(RetrievalSpecError):
        LookupSpec(**kwargs)


# ── count: type-level events ("how many workouts?") ───────────────────────────────────
def test_count_events_counts_the_type_not_a_metric(db, user_id) -> None:
    # One workout carries duration, one carries nothing aggregatable — both count.
    ids = _seed(
        db,
        [
            _mem(user_id, T0, "workout", {"activity": "run", "duration_min": 30}),
            _mem(user_id, T0 + timedelta(days=1), "workout", {"activity": "yoga"}),
            _mem(user_id, T0 + timedelta(days=2), "meal"),
        ],
    )
    result, step = _count(db, user_id, CountSpec(type="workout", start=START, end=END))
    assert result.n == 2
    assert set(result.evidence_ids) == set(ids[:2])
    assert step.row_count == 2
    assert json.loads(json.dumps(step.params))["type"] == "workout"


def test_count_zero_is_defined(db, user_id) -> None:
    result, step = _count(db, user_id, CountSpec(type="workout", start=START, end=END))
    assert result.n == 0
    assert result.evidence_ids == ()
    assert step.row_count == 0


def test_count_excludes_superseded(db, user_id) -> None:
    keep, drop = _seed(
        db,
        [
            _mem(user_id, T0, "workout", {"activity": "run"}),
            _mem(user_id, T0 + timedelta(days=1), "workout", {"activity": "row"}),
        ],
    )
    with db.transaction() as cur:
        mark_superseded(cur, user_id, drop, superseded_by=keep)
    result, _ = _count(db, user_id, CountSpec(type="workout", start=START, end=END))
    assert result.n == 1
    assert result.evidence_ids == (keep,)
