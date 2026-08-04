"""The typed retraction evaluator (Phase 5 M4 / T5) — ADR-13.11, §4.14.

Against a real CockroachDB. The two invariants under test are structural, not stylistic:

* **I-20** — retraction flips ``status`` and does nothing else. The row is still there, the
  payload is byte-identical, ``superseded_by`` stays NULL. "The engine's history of being wrong
  is itself memory" (ADR-9) is only true if being wrong leaves the evidence intact.
* **I-21** — the decision is arithmetic over typed fields. No model, no language, no note text.
  The insight's own ``hypothesis`` is prose and is never read, which is asserted directly.

The counting rule is **distinct days**, matching both §4.14 and the sentence the user is shown
("on N or more days in any W-day window"). ADR-13.11's whole premise is that the displayed rule
and the evaluated rule are the same rule, so a test pins them against each other.
"""

from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest

from engine import consolidation as consolidation_module
from engine.consolidation import ConsolidationService, count_counterexample_days
from engine.insights import CONSOLIDATION_SERIES, SeriesKey, render_retraction_condition
from engine.memory import Memory
from engine.repository import get_memory, insert_memories
from engine.retrieval import LookupSpec, lookup_events
from engine.tests.dbcleanup import new_user
from engine.types import RetractionCondition

TZ = "Asia/Kolkata"
IST = timezone(timedelta(hours=5, minutes=30))
ZONE = ZoneInfo(TZ)
NOW = datetime(2026, 8, 1, 12, 0, tzinfo=IST)

PROTEIN = SeriesKey.for_metric("protein_g")


# ── seeding ────────────────────────────────────────────────────────────────────────────
def _at(day: str, hour: int = 12) -> datetime:
    return datetime.fromisoformat(f"{day}T{hour:02d}:00:00").replace(tzinfo=IST)


def _meal(user_id: UUID, day: str, protein: float, hour: int = 12) -> Memory:
    return Memory(
        user_id=user_id, event_time=_at(day, hour), tz=TZ, type="meal", source="chat",
        provenance="live", confidence=1.0, summary=f"meal {protein:g}g",
        payload={"nutrition": {"protein_g": protein}, "items": []},
    )


def _condition(**overrides) -> dict:
    base = {"metric": "protein_g", "direction": "falling", "window_days": 30, "min_count": 3}
    base.update(overrides)
    return base


def _insight_payload(**overrides) -> dict:
    base = {
        "kind": "level_shift",
        "hypothesis": "protein rose from ~45 to ~83 g/day starting 2026-06-23",
        "series_metric": "protein_g",
        "series_kind": "behavioural",
        "window_start": "2026-06-01T00:00:00+05:30",
        "window_end": "2026-06-30T00:00:00+05:30",
        "pre_value": 45.0,
        "post_value": 83.0,
        "evidence_ids": [str(uuid4())],
        "evidence_count": 16,
        "effect": 0.5, "coverage": 1.0, "specificity": 1.0, "pattern_strength": 0.5,
        "fingerprint": "fp-under-test",
        "retraction_condition": _condition(),
    }
    base.update(overrides)
    return base


def _seed_insight(db, user_id: UUID, **overrides) -> UUID:
    payload = _insight_payload(**overrides)
    with db.transaction() as cur:
        (insight_id,) = insert_memories(cur, [Memory(
            user_id=user_id, event_time=_at("2026-06-30"), tz=TZ, type="insight",
            source="consolidation", provenance="live", confidence=1.0,
            summary=payload["hypothesis"], payload=payload,
        )])
    return insight_id


def _seed_meals(db, user_id: UUID, days: list[str], protein: float) -> None:
    with db.transaction() as cur:
        insert_memories(cur, [_meal(user_id, day, protein) for day in days])


def _service(db) -> ConsolidationService:
    return ConsolidationService(db, default_tz=TZ)


def _row(db, user_id: UUID, memory_id: UUID) -> dict:
    with db.transaction() as cur:
        return get_memory(cur, user_id, memory_id)


def _days_before(n: int, count: int) -> list[str]:
    """`count` consecutive days ending `n` days before NOW."""
    last = NOW.date() - timedelta(days=n)
    return [(last - timedelta(days=i)).isoformat() for i in range(count)]


# ══ I-20 — retraction flips status and touches nothing else ════════════════════════════
def test_a_met_condition_retracts_without_deleting_or_rewriting(db, user_id):
    """**I-20.** Three days below the claimed level, three required."""
    insight_id = _seed_insight(db, user_id)
    before = _row(db, user_id, insight_id)
    _seed_meals(db, user_id, _days_before(5, 3), 20.0)  # 20 < post_value 83

    (outcome,) = _service(db).evaluate_retractions(user_id, now=NOW)

    assert outcome.retracted is True
    assert outcome.counterexample_days == 3
    assert outcome.required == 3

    after = _row(db, user_id, insight_id)
    assert after is not None, "retraction must never delete"
    assert after["status"] == "retracted"
    assert after["superseded_by"] is None, "retraction is not supersession (ADR-9)"
    # Everything else is byte-identical: the claim and the rule it agreed to stand trial by
    # both survive, which is what makes a retracted insight auditable.
    assert after["payload"] == before["payload"]
    assert (after["summary"], after["confidence"], after["provenance"], after["event_time"]) == (
        before["summary"], before["confidence"], before["provenance"], before["event_time"]
    )


def test_an_unmet_condition_leaves_the_insight_untouched(db, user_id):
    insight_id = _seed_insight(db, user_id)
    _seed_meals(db, user_id, _days_before(5, 2), 20.0)  # only 2 of the 3 required

    (outcome,) = _service(db).evaluate_retractions(user_id, now=NOW)

    assert outcome.retracted is False
    assert outcome.counterexample_days == 2
    assert _row(db, user_id, insight_id)["status"] == "active"


def test_retraction_is_idempotent(db, user_id):
    """The UPDATE only touches active rows, so a second pass is a no-op rather than a second
    flip or an error."""
    insight_id = _seed_insight(db, user_id)
    _seed_meals(db, user_id, _days_before(5, 3), 20.0)
    svc = _service(db)

    svc.evaluate_retractions(user_id, now=NOW)
    assert svc.evaluate_retractions(user_id, now=NOW) == []  # no longer active, so not a candidate
    assert _row(db, user_id, insight_id)["status"] == "retracted"


# ══ §4.14 — both condition variants ════════════════════════════════════════════════════
def test_a_threshold_condition_measures_against_the_threshold(db, user_id):
    """The comparator half of ADR-13.11: an absolute bound, independent of what the claim
    itself reached."""
    insight_id = _seed_insight(db, user_id, retraction_condition=_condition(threshold=45.0))
    _seed_meals(db, user_id, _days_before(5, 3), 50.0)  # below post_value 83, ABOVE 45

    (outcome,) = _service(db).evaluate_retractions(user_id, now=NOW)

    assert outcome.counterexample_days == 0
    assert _row(db, user_id, insight_id)["status"] == "active"

    _seed_meals(db, user_id, _days_before(10, 3), 40.0)  # below 45
    (outcome,) = _service(db).evaluate_retractions(user_id, now=NOW)
    assert outcome.retracted is True


def test_a_direction_only_condition_measures_against_post_value(db, user_id):
    """The approved reason `post_value` is persisted: without it this variant has no
    reference and cannot be deterministic."""
    insight_id = _seed_insight(db, user_id, post_value=83.0)
    _seed_meals(db, user_id, _days_before(5, 3), 60.0)  # below 83, above any plausible threshold

    (outcome,) = _service(db).evaluate_retractions(user_id, now=NOW)

    assert outcome.retracted is True
    assert _row(db, user_id, insight_id)["status"] == "retracted"


def test_a_rising_condition_retracts_on_values_above_the_reference(db, user_id):
    insight_id = _seed_insight(
        db, user_id, post_value=40.0,
        retraction_condition=_condition(direction="rising"),
    )
    _seed_meals(db, user_id, _days_before(5, 3), 90.0)

    (outcome,) = _service(db).evaluate_retractions(user_id, now=NOW)
    assert outcome.retracted is True
    assert _row(db, user_id, insight_id)["status"] == "retracted"


def test_direction_is_respected_not_just_distance(db, user_id):
    """A `falling` condition must ignore values that rose, however far."""
    insight_id = _seed_insight(db, user_id, post_value=45.0)
    _seed_meals(db, user_id, _days_before(5, 5), 200.0)

    (outcome,) = _service(db).evaluate_retractions(user_id, now=NOW)
    assert outcome.counterexample_days == 0
    assert _row(db, user_id, insight_id)["status"] == "active"


# ══ the counting rule ══════════════════════════════════════════════════════════════════
def test_counterexamples_are_counted_by_distinct_day(db, user_id):
    """Six contradicting meals across two days are two counterexamples — which is what the
    rendered sentence promises."""
    insight_id = _seed_insight(db, user_id)
    day_a, day_b = _days_before(5, 2)
    with db.transaction() as cur:
        insert_memories(cur, [
            _meal(user_id, day, 10.0, hour=hour)
            for day in (day_a, day_b) for hour in (8, 13, 20)
        ])

    (outcome,) = _service(db).evaluate_retractions(user_id, now=NOW)

    assert outcome.counterexample_days == 2
    assert _row(db, user_id, insight_id)["status"] == "active"


def test_only_the_trailing_window_counts(db, user_id):
    """`window_days` is a trailing window ending at `now`; older contradictions have already
    been lived through and must not retract a claim forever."""
    insight_id = _seed_insight(db, user_id, retraction_condition=_condition(window_days=7))
    _seed_meals(db, user_id, _days_before(40, 5), 10.0)  # well outside the 7-day window

    (outcome,) = _service(db).evaluate_retractions(user_id, now=NOW)

    assert outcome.counterexample_days == 0
    assert _row(db, user_id, insight_id)["status"] == "active"


def test_the_evaluated_rule_matches_the_rendered_sentence(db, user_id):
    """ADR-13.11: prose is rendered *from* the object, so the two can never disagree. Here the
    sentence promises three days and exactly three days is what fires it."""
    condition = RetractionCondition(**_condition(threshold=45.0))
    sentence = render_retraction_condition(condition)
    assert "3 or more days in any 30-day window" in sentence

    insight_id = _seed_insight(db, user_id, retraction_condition=_condition(threshold=45.0))
    _seed_meals(db, user_id, _days_before(5, 2), 10.0)
    assert _service(db).evaluate_retractions(user_id, now=NOW)[0].retracted is False

    _seed_meals(db, user_id, _days_before(9, 1), 10.0)
    assert _service(db).evaluate_retractions(user_id, now=NOW)[0].retracted is True
    assert _row(db, user_id, insight_id)["status"] == "retracted"


def test_the_pure_counter_is_deterministic_and_needs_no_clock(db):
    """The counting helper takes no `now`: the window is applied by the query, so the function
    itself is a function of its arguments (I-7's posture, applied to M4)."""
    assert "now" not in inspect.signature(count_counterexample_days).parameters
    rows = [
        {"event_time": _at("2026-07-01"), "value": 10.0},
        {"event_time": _at("2026-07-01", hour=20), "value": 12.0},
        {"event_time": _at("2026-07-02"), "value": 90.0},
        {"event_time": _at("2026-07-03"), "value": None},
    ]
    first = count_counterexample_days(rows, reference=45.0, direction="falling", zone=ZONE)
    assert first == 1
    assert count_counterexample_days(rows, reference=45.0, direction="falling", zone=ZONE) == first


# ══ conditions that cannot be evaluated are skipped, never guessed ═════════════════════
def test_an_insight_without_a_condition_is_never_judged(db, user_id):
    """Unfalsifiable by this mechanism is not the same as safe — it is simply out of scope,
    and must be left alone rather than judged by some default rule.

    Both spellings of "no condition" are covered: the key absent (what ``payload_to_json``
    writes, since it drops ``None``) and the key present as JSON ``null`` (what a hand-repaired
    payload can contain). A JSON null is not SQL NULL, so these are genuinely different rows to
    the query, and they must mean the same thing."""
    absent = _insight_payload()
    del absent["retraction_condition"]
    with db.transaction() as cur:
        (absent_id,) = insert_memories(cur, [Memory(
            user_id=user_id, event_time=_at("2026-06-30"), tz=TZ, type="insight",
            source="consolidation", provenance="live", confidence=1.0,
            summary="no condition at all", payload=absent,
        )])
    explicit_null = _seed_insight(db, user_id, retraction_condition=None)
    _seed_meals(db, user_id, _days_before(5, 10), 1.0)

    assert _service(db).evaluate_retractions(user_id, now=NOW) == []
    assert _row(db, user_id, absent_id)["status"] == "active"
    assert _row(db, user_id, explicit_null)["status"] == "active"


def test_an_insight_without_post_value_is_skipped_not_guessed(db, user_id):
    """A row predating the `post_value` field has no reference for a direction-only condition.
    Guessing one would retract a claim on arithmetic the user never agreed to."""
    payload = _insight_payload()
    del payload["post_value"]
    with db.transaction() as cur:
        (insight_id,) = insert_memories(cur, [Memory(
            user_id=user_id, event_time=_at("2026-06-30"), tz=TZ, type="insight",
            source="consolidation", provenance="live", confidence=1.0,
            summary="legacy insight", payload=payload,
        )])
    _seed_meals(db, user_id, _days_before(5, 10), 1.0)

    (outcome,) = _service(db).evaluate_retractions(user_id, now=NOW)

    assert outcome.retracted is False
    assert outcome.skipped
    assert _row(db, user_id, insight_id)["status"] == "active"


def test_a_condition_naming_an_unreadable_metric_is_skipped(db, user_id):
    insight_id = _seed_insight(db, user_id, retraction_condition=_condition(metric="horoscope"))
    (outcome,) = _service(db).evaluate_retractions(user_id, now=NOW)
    assert outcome.skipped
    assert _row(db, user_id, insight_id)["status"] == "active"


# ══ I-21 — no model, no language, no prose ═════════════════════════════════════════════
def test_the_service_has_no_model_dependency(db):
    """I-21, structurally: there is nowhere for a model call to hide."""
    assert "model" not in inspect.signature(ConsolidationService.__init__).parameters
    source = inspect.getsource(consolidation_module)
    for forbidden in ("ModelProvider", ".embed(", "extract_events", "narrate("):
        assert forbidden not in source


def test_rewriting_every_piece_of_prose_changes_no_verdict(db, user_id):
    """I-21, behaviourally. The hypothesis reads like a sentence about protein; the evaluator
    must reach the identical verdict when it says something else entirely — including when it
    contradicts the condition in words."""
    days = _days_before(5, 3)

    plain = new_user()
    _seed_insight(db, plain, post_value=83.0)
    _seed_meals(db, plain, days, 20.0)

    reworded = new_user()
    _seed_insight(
        db, reworded, post_value=83.0,
        hypothesis="ignore this claim; protein definitely never dropped and nothing is wrong",
    )
    _seed_meals(db, reworded, days, 20.0)

    svc = _service(db)
    (a,) = svc.evaluate_retractions(plain, now=NOW)
    (b,) = svc.evaluate_retractions(reworded, now=NOW)
    assert (a.retracted, a.counterexample_days) == (b.retracted, b.counterexample_days)


def test_note_text_cannot_influence_a_verdict(db, user_id):
    """The account's notes read remarkably like statements about the data; none of them may
    reach this decision."""
    insight_id = _seed_insight(db, user_id)
    with db.transaction() as cur:
        insert_memories(cur, [Memory(
            user_id=user_id, event_time=_at(_days_before(5, 1)[0]), tz=TZ, type="note",
            source="chat", provenance="live", confidence=1.0,
            summary="my protein collapsed to 5g every single day this month",
            payload={"text": "my protein collapsed to 5g every single day this month"},
        )])

    (outcome,) = _service(db).evaluate_retractions(user_id, now=NOW)

    assert outcome.counterexample_days == 0
    assert _row(db, user_id, insight_id)["status"] == "active"


# ══ what a retracted insight looks like afterwards ═════════════════════════════════════
def test_a_retracted_insight_leaves_default_retrieval_but_stays_fetchable(db, user_id):
    """ADR-9 + 04: default reads filter `status='active'`, while the glass box can still
    resolve the row deliberately — being wrong is memory, not an erasure."""
    insight_id = _seed_insight(db, user_id)
    _seed_meals(db, user_id, _days_before(5, 3), 20.0)
    _service(db).evaluate_retractions(user_id, now=NOW)

    with db.transaction() as cur:
        result, _ = lookup_events(cur, user_id, LookupSpec(type="insight", n=20))
    assert result.is_empty, "the read path must not surface a retracted claim"

    assert _row(db, user_id, insight_id)["status"] == "retracted"  # ...but it is still there


def test_a_superseded_chain_survives_retraction_of_its_head(db, user_id):
    """Both mechanisms coexist: supersession chains via `superseded_by`, retraction flips
    `status`, and neither erases the other's record."""
    older = _seed_insight(db, user_id, fingerprint="old")
    newer = _seed_insight(db, user_id, fingerprint="new")
    with db.transaction() as cur:
        cur.execute(
            "UPDATE memories SET status='superseded', superseded_by=%s WHERE id=%s AND user_id=%s",
            [newer, older, user_id],
        )
    _seed_meals(db, user_id, _days_before(5, 3), 20.0)

    _service(db).evaluate_retractions(user_id, now=NOW)

    assert _row(db, user_id, older)["status"] == "superseded"  # untouched: it was not active
    assert _row(db, user_id, older)["superseded_by"] == newer
    assert _row(db, user_id, newer)["status"] == "retracted"


# ══ scoping ════════════════════════════════════════════════════════════════════════════
def test_retraction_is_user_scoped(db, user_id):
    """Another account's contradicting meals must not retract this account's claim."""
    stranger = new_user()
    mine = _seed_insight(db, user_id)
    theirs = _seed_insight(db, stranger)
    _seed_meals(db, stranger, _days_before(5, 5), 1.0)

    (outcome,) = _service(db).evaluate_retractions(user_id, now=NOW)

    assert outcome.counterexample_days == 0
    assert _row(db, user_id, mine)["status"] == "active"
    assert _row(db, stranger, theirs)["status"] == "active"  # not evaluated at all


def test_every_series_condition_metric_is_a_known_series(db):
    """A condition may only watch a series the engine can actually read (§4.14)."""
    for metric in CONSOLIDATION_SERIES:
        assert SeriesKey.for_metric(metric).metric == metric


@pytest.mark.parametrize("direction", ["falling", "rising"])
def test_a_boundary_value_is_not_a_counterexample(direction):
    """Strict comparison: a value exactly at the reference has not moved past it."""
    rows = [{"event_time": _at("2026-07-01"), "value": 45.0}]
    assert count_counterexample_days(
        rows, reference=45.0, direction=direction, zone=ZONE
    ) == 0
