"""Aggregation builder family tests (M1/Task 2 — 12-test-plan.md `engine/retrieval`
aggregate block: sum/avg, day/week grouping, type+date filters, EMPTY RESULT, tz edges).

Runs against real single-node CockroachDB (ADR-13.8) via the shared `db` fixture; spec
validation runs DB-free. The flagship test writes through the real Phase 2 ingestion
pipeline and reads back through the builder — write path and read path meeting for the
first time.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest

from engine.db import Database
from engine.ingestion import IngestionService
from engine.memory import Memory
from engine.model import ExtractedEvent
from engine.repository import insert_memories, mark_superseded
from engine.retrieval import AggregateSpec, RetrievalSpecError, aggregate_memories
from engine.tests.conftest import FakeModelProvider
from engine.tests.dbcleanup import new_user

UTC = timezone.utc
IST = "Asia/Kolkata"
# July 2026, [start, end) — all fixtures live inside unless a test says otherwise.
START = datetime(2026, 7, 1, tzinfo=UTC)
END = datetime(2026, 8, 1, tzinfo=UTC)


def _meal(user_id: UUID, event_time: datetime, protein: float | None = None) -> Memory:
    payload: dict = {"meal_type": "lunch"}
    if protein is not None:
        payload["nutrition"] = {"protein_g": protein}
    return Memory(
        user_id=user_id,
        event_time=event_time,
        tz=IST,
        type="meal",
        source="chat",
        provenance="live",
        confidence=0.9,
        payload=payload,
    )


def _seed(db: Database, memories: list[Memory]) -> list[UUID]:
    with db.transaction() as cur:
        return insert_memories(cur, memories)


def _spec(**overrides) -> AggregateSpec:
    defaults = dict(metric="protein_g", start=START, end=END, tz=IST)
    return AggregateSpec(**{**defaults, **overrides})


def _run(db: Database, user_id: UUID, spec: AggregateSpec):
    with db.transaction() as cur:
        return aggregate_memories(cur, user_id, spec)


# ── aggregation semantics ─────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("agg", "expected"),
    [("sum", 90.0), ("avg", 30.0), ("count", 3.0), ("min", 20.0), ("max", 40.0)],
)
def test_aggregations_over_typed_payloads(db, user_id, agg, expected) -> None:
    t = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
    in_range = [
        _meal(user_id, t, 30),
        _meal(user_id, t + timedelta(hours=5), 40),
        _meal(user_id, t + timedelta(days=1), 20),
    ]
    ids = _seed(db, in_range + [_meal(user_id, datetime(2026, 6, 15, tzinfo=UTC), 99)])

    result, step = _run(db, user_id, _spec(agg=agg))

    assert not result.is_empty
    (bucket,) = result.buckets
    assert bucket.bucket is None  # ungrouped total
    assert bucket.value == pytest.approx(expected)
    assert bucket.n == 3
    assert set(bucket.evidence_ids) == set(ids[:3])  # out-of-range row never cited
    assert step.row_count == 3


def test_group_by_day_buckets_in_the_users_timezone(db, user_id) -> None:
    # Two meals 1h apart, SAME UTC day, DIFFERENT IST days:
    #   23:30 IST Jul 19 == 18:00 UTC Jul 19;  00:30 IST Jul 20 == 19:00 UTC Jul 19.
    _seed(
        db,
        [
            _meal(user_id, datetime(2026, 7, 19, 18, 0, tzinfo=UTC), 30),
            _meal(user_id, datetime(2026, 7, 19, 19, 0, tzinfo=UTC), 40),
        ],
    )

    ist_result, _ = _run(db, user_id, _spec(group_by="day"))
    assert [(b.bucket, b.value) for b in ist_result.buckets] == [
        ("2026-07-19", 30.0),
        ("2026-07-20", 40.0),
    ]

    # The same rows bucketed in UTC collapse to one day — the tz edge, made visible.
    utc_result, _ = _run(db, user_id, _spec(group_by="day", tz="UTC"))
    assert [(b.bucket, b.value) for b in utc_result.buckets] == [("2026-07-19", 70.0)]


def test_group_by_week_starts_iso_monday(db, user_id) -> None:
    anchor = datetime(2026, 7, 8, 12, 0, tzinfo=UTC)
    monday = anchor - timedelta(days=anchor.weekday())  # that week's Monday, noon
    _seed(
        db,
        [
            _meal(user_id, monday, 10),
            _meal(user_id, monday + timedelta(days=6), 20),  # Sunday, same ISO week
            _meal(user_id, monday + timedelta(days=7), 40),  # next Monday
        ],
    )

    result, _ = _run(db, user_id, _spec(group_by="week", tz="UTC"))

    assert [(b.bucket, b.value, b.n) for b in result.buckets] == [
        (monday.date().isoformat(), 30.0, 2),
        ((monday + timedelta(days=7)).date().isoformat(), 40.0, 1),
    ]


def test_date_range_is_half_open(db, user_id) -> None:
    _seed(
        db,
        [
            _meal(user_id, START, 10),  # exactly at start → included
            _meal(user_id, END, 99),  # exactly at end → excluded
        ],
    )
    result, _ = _run(db, user_id, _spec())
    (bucket,) = result.buckets
    assert bucket.value == 10.0
    assert bucket.n == 1


# ── the honest edges ──────────────────────────────────────────────────────────────────
def test_empty_result_is_a_defined_shape(db, user_id) -> None:
    for group_by in ("none", "day", "week"):
        result, step = _run(db, user_id, _spec(group_by=group_by))
        assert result.is_empty
        assert result.buckets == ()
        assert step.row_count == 0


def test_superseded_rows_never_double_count(db, user_id) -> None:
    # The Phase 2 reprocess contract meets the read path: a superseded row must vanish
    # from every aggregate, or a retried note would count twice.
    keep, drop = _seed(
        db,
        [
            _meal(user_id, datetime(2026, 7, 10, tzinfo=UTC), 30),
            _meal(user_id, datetime(2026, 7, 11, tzinfo=UTC), 40),
        ],
    )
    with db.transaction() as cur:
        mark_superseded(cur, user_id, drop, superseded_by=keep)

    result, _ = _run(db, user_id, _spec())
    (bucket,) = result.buckets
    assert bucket.value == 30.0
    assert bucket.evidence_ids == (keep,)


def test_rows_missing_the_metric_are_excluded(db, user_id) -> None:
    with_metric = _meal(user_id, datetime(2026, 7, 10, tzinfo=UTC), 30)
    without_metric = _meal(user_id, datetime(2026, 7, 11, tzinfo=UTC), protein=None)
    ids = _seed(db, [with_metric, without_metric])

    result, _ = _run(db, user_id, _spec(agg="count"))
    (bucket,) = result.buckets
    assert bucket.n == 1
    assert bucket.evidence_ids == (ids[0],)


def test_wrong_type_is_never_aggregated(db, user_id) -> None:
    weight = Memory(
        user_id=user_id,
        event_time=datetime(2026, 7, 10, tzinfo=UTC),
        tz=IST,
        type="weight",
        source="chat",
        provenance="live",
        confidence=0.9,
        payload={"weight_kg": 78.0},
    )
    _seed(db, [weight])
    assert _run(db, user_id, _spec(metric="protein_g"))[0].is_empty
    # ...while the matching metric sees it.
    result, _ = _run(db, user_id, _spec(metric="weight_kg"))
    assert result.buckets[0].value == 78.0


def test_cross_user_isolation(db, user_id) -> None:
    other = new_user()
    _seed(db, [_meal(other, datetime(2026, 7, 10, tzinfo=UTC), 500)])
    _seed(db, [_meal(user_id, datetime(2026, 7, 10, tzinfo=UTC), 30)])

    result, _ = _run(db, user_id, _spec())
    (bucket,) = result.buckets
    assert bucket.value == 30.0  # the other user's 500g never leaks in


# ── slot validation (DB-free: the planner's mistakes die above the database) ──────────
@pytest.mark.parametrize(
    "overrides",
    [
        {"metric": "cholesterol"},  # not in the whitelist
        {"agg": "median"},
        {"group_by": "month"},
        {"tz": "Mars/Olympus_Mons"},
        {"start": datetime(2026, 7, 1)},  # naive datetime
        {"start": END, "end": START},  # inverted range
        {"start": START, "end": START},  # empty range
    ],
)
def test_invalid_slots_raise_before_any_sql(overrides) -> None:
    with pytest.raises(RetrievalSpecError):
        _spec(**overrides)


def test_step_records_the_executed_query_without_interpolation(db, user_id) -> None:
    import json

    _seed(db, [_meal(user_id, datetime(2026, 7, 10, tzinfo=UTC), 30)])
    _, step = _run(db, user_id, _spec(group_by="day"))

    assert step.family == "aggregate"
    # Placeholders, not values: the executed SQL string never contains runtime data.
    for placeholder in ("%(user_id)s", "%(type)s", "%(start)s", "%(path)s", "%(tz)s"):
        assert placeholder in step.sql
    assert str(user_id) not in step.sql
    # The trace-bound params are JSON-ready as-is (RetrievalStep contract).
    assert json.loads(json.dumps(step.params))["user_id"] == str(user_id)
    assert step.params["path"] == ["nutrition", "protein_g"]


# ── flagship: the Phase 2 write path feeds the Phase 3 read path ──────────────────────
def test_ingested_meals_are_aggregatable(db, user_id) -> None:
    events = [
        ExtractedEvent(
            type="meal",
            event_time=datetime(2026, 7, 20, 8, 0, tzinfo=UTC),
            tz=IST,
            confidence=0.9,
            summary="breakfast: 3 eggs",
            payload={"meal_type": "breakfast", "nutrition": {"protein_g": 18, "kcal": 210}},
        ),
        ExtractedEvent(
            type="meal",
            event_time=datetime(2026, 7, 20, 13, 0, tzinfo=UTC),
            tz=IST,
            confidence=0.9,
            summary="lunch: 200g chicken",
            payload={"meal_type": "lunch", "nutrition": {"protein_g": 60, "kcal": 330}},
        ),
        ExtractedEvent(
            type="meal",
            event_time=datetime(2026, 7, 20, 20, 0, tzinfo=UTC),
            tz=IST,
            confidence=0.9,
            summary="dinner: 250g curd",
            payload={"meal_type": "dinner", "nutrition": {"protein_g": 27, "kcal": 240}},
        ),
    ]
    svc = IngestionService(db, FakeModelProvider(events), default_tz=IST)
    receipt = svc.ingest_text(user_id, "logged breakfast, lunch and dinner")
    assert receipt.parse_status == "ok"
    created = {ref.id for ref in receipt.created}

    result, step = _run(db, user_id, _spec())
    (bucket,) = result.buckets
    assert bucket.value == pytest.approx(105.0)
    assert bucket.n == 3
    # The computed number cites exactly the rows the receipt says were created.
    assert set(bucket.evidence_ids) == created
    assert step.row_count == 3
