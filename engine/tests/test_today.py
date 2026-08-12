"""Today snapshot tests — the honesty contract, not the layout.

Deliberately narrow. Most of ``engine/today.py`` is composition over paths that already have
their own suites (`test_retrieval_aggregate`, `test_profile`, `test_glassbox`), and re-testing
those here would be duplication. What is genuinely new, and what would be invisible if it
regressed, is the **null-versus-zero distinction**: a metric with no logged rows must serialize
as ``None``, because "you have logged nothing" and "you logged zero grams" are different claims
and only one of them is true at 8 AM. Everything else in this file exists to pin the boundaries
around that (a real logged zero, the day split, and the insight/recent separation).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

from engine.db import Database
from engine.memory import Memory
from engine.repository import insert_memories
from engine.today import build_today

UTC = timezone.utc
IST = "Asia/Kolkata"
#: 2026-07-15 06:00 UTC = 11:30 IST, comfortably mid-morning on the 15th in the user's zone,
#: so "today" and "yesterday" are unambiguous without depending on when the suite runs.
NOW = datetime(2026, 7, 15, 6, 0, tzinfo=UTC)


def _meal(user_id: UUID, event_time: datetime, protein: float | None) -> Memory:
    payload: dict = {"meal_type": "lunch"}
    if protein is not None:
        payload["nutrition"] = {"protein_g": protein, "kcal": protein * 10}
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


def _build(db: Database, user_id: UUID):
    with db.transaction() as cur:
        return build_today(cur, user_id, now=NOW, tz=IST)


# ── the contract ──────────────────────────────────────────────────────────────────────
def test_nothing_logged_is_none_not_zero(db, user_id) -> None:
    """The rule the whole surface rests on. An empty account must not report 0 g of protein."""
    snapshot = _build(db, user_id)

    assert snapshot.today_protein.value is None
    assert snapshot.today_protein.n == 0
    assert snapshot.today_protein.has_data is False
    assert snapshot.today_kcal.value is None
    assert snapshot.yesterday_protein.value is None


def test_a_real_logged_zero_is_zero_not_none(db, user_id) -> None:
    """The other half, and the reason ``has_data`` exists as a separate field: a meal that
    genuinely carried 0 g of protein is data, and must be distinguishable from no meal."""
    _seed(db, [_meal(user_id, datetime(2026, 7, 15, 4, 0, tzinfo=UTC), 0.0)])

    snapshot = _build(db, user_id)

    assert snapshot.today_protein.value == 0.0
    assert snapshot.today_protein.n == 1
    assert snapshot.today_protein.has_data is True


def test_today_and_yesterday_split_on_the_users_local_midnight(db, user_id) -> None:
    """Both meals are on 2026-07-14 in UTC; only one of them is 'yesterday' in IST.

    22:00 UTC on the 14th is 03:30 IST on the **15th** — today. Getting this wrong would show
    the user last night's dinner as part of today's total, which is the kind of quiet error a
    person notices and cannot explain.
    """
    _seed(
        db,
        [
            _meal(user_id, datetime(2026, 7, 14, 8, 0, tzinfo=UTC), 40.0),  # 13:30 IST, 14th
            _meal(user_id, datetime(2026, 7, 14, 22, 0, tzinfo=UTC), 25.0),  # 03:30 IST, 15th
        ],
    )

    snapshot = _build(db, user_id)

    assert snapshot.today_protein.value == 25.0
    assert snapshot.yesterday_protein.value == 40.0


def test_coverage_counts_distinct_days_not_meals(db, user_id) -> None:
    """Three meals across two days is a coverage of 2 — the number is about consistency, and
    counting rows would make a single heavy day look like a well-logged week."""
    _seed(
        db,
        [
            _meal(user_id, NOW - timedelta(days=1), 30.0),
            _meal(user_id, NOW - timedelta(days=1, hours=4), 30.0),
            _meal(user_id, NOW - timedelta(days=3), 30.0),
        ],
    )

    snapshot = _build(db, user_id)

    assert snapshot.days_logged_last_7 == 2


def test_recent_holds_only_logged_health_events(db, user_id) -> None:
    """The strip is what the *user* logged. An insight is a claim the engine made, and a
    `profile_change` is a settings edit that arrives one row per field — onboarding alone writes
    enough of them to bury a week of meals. Both are real memories and neither belongs here."""
    _seed(
        db,
        [
            _meal(user_id, NOW - timedelta(hours=2), 46.0),
            Memory(
                user_id=user_id,
                event_time=NOW - timedelta(hours=1),
                tz=IST,
                type="profile_change",
                source="profile",
                provenance="live",
                confidence=1.0,
                summary="activity level set to moderate",
                payload={"field": "activity_level", "new_value": "moderate"},
            ),
        ],
    )

    snapshot = _build(db, user_id)

    assert [r["type"] for r in snapshot.recent] == ["meal"]
    assert snapshot.insight is None


def test_every_figure_carries_its_query(db, user_id) -> None:
    """Today asserts numbers the user never asked for, so it owes the same "how this was
    retrieved" affordance a conversational turn does (ADR-12)."""
    _seed(db, [_meal(user_id, NOW - timedelta(hours=2), 46.0)])

    snapshot = _build(db, user_id)

    assert len(snapshot.steps) == 5
    assert all(step.sql for step in snapshot.steps)
