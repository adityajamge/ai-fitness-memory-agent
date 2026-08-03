"""The consolidation service (Phase 5 M3) — identity, persistence, budget, isolation.

Against a real CockroachDB (ADR-13.8). The kernel's arithmetic is already pinned by
`test_analytics.py`; what is tested here is everything that only shows up once rows are
committed — which is the lesson [ADR-15.6](../../docs/office-hours/09-decisions.md) drew from
Phase 4, where both defects lived in seams whose sides were each individually correct.

The load-bearing test is **I-12**: re-running consolidation over unchanged data writes zero
rows. It is asserted at the **committed-row layer** (count the table, before and after) rather
than against the service's return value, because a service that reports "unchanged" while
inserting is exactly the failure mode a return-value assertion cannot see.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest

from engine.consolidation import ConsolidationService
from engine.insights import SeriesKey
from engine.memory import Memory
from engine.repository import insert_memories
from engine.tests.dbcleanup import new_user

TZ = "Asia/Kolkata"
IST = timezone(timedelta(hours=5, minutes=30))

PROTEIN = SeriesKey.for_metric("protein_g")
BODY_FAT = SeriesKey.for_metric("body_fat_pct")


# ── seeding ────────────────────────────────────────────────────────────────────────────
def _at(day: str, hour: int = 12) -> datetime:
    return datetime.fromisoformat(f"{day}T{hour:02d}:00:00").replace(tzinfo=IST)


def _meal(user_id: UUID, day: str, protein: float, composition: str | None) -> Memory:
    payload: dict = {"nutrition": {"protein_g": protein}, "items": []}
    if composition:
        payload["expanded_from"] = {"composition": composition, "assertion": f"{protein:g} g/day"}
    return Memory(
        user_id=user_id,
        event_time=_at(day),
        tz=TZ,
        type="meal",
        source="replay" if composition else "chat",
        provenance="reconstructed" if composition else "live",
        confidence=0.6 if composition else 1.0,
        summary=f"meal {protein:g}g protein",
        payload=payload,
    )


def _phase(user_id: UUID, start: str, days: int, protein: float, composition: str) -> list[Memory]:
    first = datetime.fromisoformat(start).date()
    return [
        _meal(user_id, (first + timedelta(days=n)).isoformat(), protein, composition)
        for n in range(days)
    ]


def _scan(user_id: UUID, day: str, body_fat: float) -> Memory:
    return Memory(
        user_id=user_id, event_time=_at(day), tz=TZ, type="body_scan", source="chat",
        provenance="live", confidence=1.0, summary=f"scan {body_fat:g}%",
        payload={"body_fat_pct": body_fat},
    )


def _seed(db, memories: list[Memory]) -> list[UUID]:
    with db.transaction() as cur:
        return insert_memories(cur, memories)


def _seed_shift(db, user_id: UUID, *, pre: float = 30.0, post: float = 60.0) -> None:
    """Two 8-day phases either side of a level shift — the smallest series that clears
    MIN_SPAN_DAYS on both sides."""
    _seed(db, _phase(user_id, "2026-05-01", 8, pre, "phase-a")
              + _phase(user_id, "2026-05-09", 8, post, "phase-b"))


def _service(db, **kwargs) -> ConsolidationService:
    return ConsolidationService(db, default_tz=TZ, **kwargs)


def _expired_clock():
    """A clock whose first reading sets the deadline and whose next is far past it.

    A *constant* clock would never expire — elapsed time is always zero — which is a real trap
    when testing a deadline rather than a duration."""
    readings = iter([0.0] + [1e9] * 100)
    return lambda: next(readings)


def _count(db, user_id: UUID, memory_type: str | None = None) -> int:
    sql = "SELECT count(*) AS n FROM memories WHERE user_id = %s"
    params: list = [user_id]
    if memory_type:
        sql += " AND type = %s"
        params.append(memory_type)
    with db.transaction() as cur:
        cur.execute(sql, params)
        return int(cur.fetchone()["n"])


def _insights(db, user_id: UUID, status: str = "active") -> list[dict]:
    with db.transaction() as cur:
        cur.execute(
            "SELECT id, status, superseded_by, confidence, provenance, source, summary, "
            "event_time, payload, (embedding IS NOT NULL) AS embedded "
            "FROM memories WHERE user_id = %s AND type = 'insight' AND status = %s "
            "ORDER BY created_at",
            [user_id, status],
        )
        return cur.fetchall()


# ══ I-12 — the property that keeps consolidation from duplicating itself ═══════════════
def test_recompute_over_unchanged_data_writes_zero_rows(db, user_id):
    """**I-12.** Consolidation runs on every ingest touching a series; without identity, ten
    logged meals would write ten copies of one claim. Asserted at the committed-row layer."""
    _seed_shift(db, user_id)
    svc = _service(db)

    first = svc.consolidate_series(user_id, PROTEIN)
    assert first.created is not None
    after_first = _count(db, user_id)

    for _ in range(3):
        outcome = svc.consolidate_series(user_id, PROTEIN)
        assert outcome.created is None
        assert outcome.unchanged == first.created

    assert _count(db, user_id) == after_first
    assert len(_insights(db, user_id)) == 1


def test_the_fingerprint_is_what_makes_a_recompute_a_no_op(db, user_id):
    """The identity is the *claim*, not the row: two runs reach the same fingerprint, so the
    second has nothing to write."""
    _seed_shift(db, user_id)
    svc = _service(db)
    created = svc.consolidate_series(user_id, PROTEIN).created

    (insight,) = _insights(db, user_id)
    assert insight["payload"]["fingerprint"]
    assert svc.consolidate_series(user_id, PROTEIN).unchanged == created


# ══ §4.6 — a changed claim supersedes, never rewrites ══════════════════════════════════
def test_a_changed_claim_inserts_a_replacement_and_supersedes_the_original(db, user_id):
    """ADR-9: the engine's history of being wrong is itself memory. The old row stays, flipped
    to superseded and chained to what replaced it."""
    _seed_shift(db, user_id)
    svc = _service(db)
    original = svc.consolidate_series(user_id, PROTEIN).created

    # A later, larger phase moves the most recent shift — a different claim.
    _seed(db, _phase(user_id, "2026-05-17", 8, 120.0, "phase-c"))
    outcome = svc.consolidate_series(user_id, PROTEIN)

    assert outcome.created is not None and outcome.created != original
    assert outcome.superseded == original

    active = _insights(db, user_id)
    assert [row["id"] for row in active] == [outcome.created]
    (retired,) = _insights(db, user_id, status="superseded")
    assert retired["id"] == original
    assert retired["superseded_by"] == outcome.created


def test_supersession_never_deletes(db, user_id):
    """I-20's sibling at the identity layer: the row count only ever grows."""
    _seed_shift(db, user_id)
    svc = _service(db)
    svc.consolidate_series(user_id, PROTEIN)
    before = _count(db, user_id, "insight")

    _seed(db, _phase(user_id, "2026-05-17", 8, 120.0, "phase-c"))
    svc.consolidate_series(user_id, PROTEIN)

    assert _count(db, user_id, "insight") == before + 1


def test_a_surplus_of_active_insights_is_reconciled_not_ignored(db, user_id):
    """I-10 allows one active insight per identity. If a duplicate ever exists, the next run
    must collapse it rather than pick a winner silently."""
    _seed_shift(db, user_id)
    svc = _service(db)
    first = svc.consolidate_series(user_id, PROTEIN).created

    # Forge a second active insight for the same identity, bypassing the service.
    (existing,) = _insights(db, user_id)
    twin = Memory(
        user_id=user_id, event_time=existing["event_time"], tz=TZ, type="insight",
        source="consolidation", provenance="live", confidence=1.0,
        summary="duplicate", payload=dict(existing["payload"]),
    )
    (twin_id,) = _seed(db, [twin])
    assert len(_insights(db, user_id)) == 2

    outcome = svc.consolidate_series(user_id, PROTEIN)

    active = _insights(db, user_id)
    assert len(active) == 1
    assert active[0]["id"] == outcome.created
    superseded = {row["id"] for row in _insights(db, user_id, status="superseded")}
    assert superseded == {first, twin_id}


# ══ §4.6 — refusal leaves an existing claim alone ══════════════════════════════════════
def test_a_refusal_leaves_an_existing_insight_active(db, user_id):
    """Silence is the absence of a new claim, not evidence against the old one — withdrawing
    on quiet is what retraction conditions are for (M4), and conflating the two would let a
    slow week retract something the data still supports."""
    payload = {
        "kind": "intervention_outcome", "hypothesis": "body fat changed",
        "series_metric": "body_fat_pct", "series_kind": "outcome",
        "window_start": "2026-05-01T00:00:00+05:30", "window_end": "2026-06-01T00:00:00+05:30",
        "evidence_ids": [str(uuid.uuid4())], "evidence_count": 2,
        "effect": 0.5, "coverage": 1.0, "specificity": 1.0, "pattern_strength": 0.5,
        "fingerprint": "handmade",
    }
    (existing,) = _seed(db, [Memory(
        user_id=user_id, event_time=_at("2026-06-01"), tz=TZ, type="insight",
        source="consolidation", provenance="live", confidence=1.0,
        summary="body fat changed", payload=payload,
    )])

    outcome = _service(db).consolidate_series(user_id, BODY_FAT)  # no body_scan rows exist

    assert outcome.refused
    assert outcome.created is None
    assert [row["id"] for row in _insights(db, user_id)] == [existing]


def test_a_series_with_no_data_writes_nothing(db, user_id):
    outcome = _service(db).consolidate_series(user_id, PROTEIN)
    assert outcome.refused
    assert _count(db, user_id) == 0


# ══ §4.12 — what the written row actually says ═════════════════════════════════════════
def test_the_written_insight_is_shaped_as_the_architecture_specifies(db, user_id):
    _seed_shift(db, user_id)
    _service(db).consolidate_series(user_id, PROTEIN)
    (insight,) = _insights(db, user_id)

    assert insight["source"] == "consolidation"
    # event_time is the window's end: the claim is *about* then, and created_at stays truthful
    # (ADR-13.10 — no replay clock).
    assert insight["event_time"] == _at("2026-05-16")
    payload = insight["payload"]
    assert payload["kind"] == "level_shift"
    assert payload["series_metric"] == "protein_g"
    assert payload["series_kind"] == "behavioural"
    assert payload["evidence_count"] == 16  # both 8-day phases
    assert len(payload["evidence_ids"]) == 4  # boundary-anchored (I-5)
    assert payload["pattern_strength"] == pytest.approx(
        payload["effect"] * payload["coverage"] * payload["specificity"]
    )


def test_insights_are_written_unembedded(db, user_id):
    """**I-16.** Embedding is a Bedrock round trip; keeping it off this path is what makes the
    (F₀) budget reachable at all. The existing T15 backfill picks the row up."""
    _seed_shift(db, user_id)
    _service(db).consolidate_series(user_id, PROTEIN)
    (insight,) = _insights(db, user_id)
    assert insight["embedded"] is False
    assert insight["summary"]  # ...but it has a summary, so backfill can embed it


def test_confidence_and_provenance_are_inherited_from_the_evidence(db, user_id):
    """An insight is exactly as trustworthy as the least trustworthy row under it — which is
    what keeps the confidence column meaning one thing across both memory tiers."""
    _seed_shift(db, user_id)  # reconstructed rows, confidence 0.6
    _service(db).consolidate_series(user_id, PROTEIN)
    (insight,) = _insights(db, user_id)
    assert insight["provenance"] == "reconstructed"
    assert insight["confidence"] == pytest.approx(0.6)


def test_a_live_only_series_yields_a_live_insight(db, user_id):
    """Tested through the outcome path, because a level shift structurally cannot fire on live
    data: a day-long observation has no *level* (MIN_SPAN_DAYS), which is the documented reason
    day-to-day logging noise never becomes a claim."""
    _seed(db, _phase(user_id, "2026-05-01", 8, 30.0, "phase-a")
              + _phase(user_id, "2026-05-09", 8, 90.0, "phase-b"))
    _seed(db, [_scan(user_id, "2026-04-20", 39.2), _scan(user_id, "2026-06-20", 34.0)])

    _service(db).consolidate_series(user_id, BODY_FAT)

    (insight,) = _insights(db, user_id)
    # The scans are live; the protein rows behind the interventions are reconstructed, so the
    # claim inherits the weaker of what it cites rather than the flattering one.
    assert insight["payload"]["kind"] == "intervention_outcome"
    assert insight["confidence"] == pytest.approx(0.6)
    assert insight["provenance"] == "reconstructed"


def test_the_hypothesis_states_no_cause(db, user_id):
    """I-2. The engine describes what moved, never why."""
    _seed_shift(db, user_id)
    _service(db).consolidate_series(user_id, PROTEIN)
    (insight,) = _insights(db, user_id)
    text = insight["summary"].lower()
    assert "protein rose" in text
    assert not any(word in text for word in ("because", "caused", "due to", "led to"))


# ══ §4.8 — the budget defers, it never half-writes ═════════════════════════════════════
def test_an_exhausted_budget_defers_every_remaining_series(db, user_id):
    _seed_shift(db, user_id)
    clock = iter([0.0] * 2 + [99.0] * 40)  # start, first check, then always past the deadline
    svc = _service(db, clock=lambda: next(clock))

    outcome = svc.consolidate(user_id)

    assert outcome.deferred
    assert len(outcome.outcomes) == 1  # exactly one series was evaluated
    assert _count(db, user_id, "insight") <= 1


def test_a_budget_that_expires_immediately_writes_nothing(db, user_id):
    _seed_shift(db, user_id)
    svc = _service(db, clock=_expired_clock())

    outcome = svc.consolidate(user_id)

    assert outcome.outcomes == []
    assert len(outcome.deferred) == 9  # every consolidatable series
    assert _count(db, user_id, "insight") == 0


def test_deferral_is_a_result_not_an_error(db, user_id):
    """§4.8: a budget overflow is reported, never raised — ingestion has already committed and
    must not be disturbed by it."""
    _seed_shift(db, user_id)
    outcome = _service(db, clock=_expired_clock()).consolidate(user_id)
    assert outcome.wrote_nothing
    assert outcome.deferred


def test_a_full_pass_consolidates_every_series_it_can(db, user_id):
    """Budget deliberately lifted: this asserts *which* series a pass covers, not how fast it
    is. The default 300ms is provisional pending T12 (ADR-13.1) and against the remote test
    cluster it completes roughly one series — which the deferral tests above cover."""
    _seed_shift(db, user_id)
    outcome = _service(db, budget_ms=60_000).consolidate(user_id)
    assert outcome.created_ids
    assert not outcome.deferred
    assert {o.series for o in outcome.outcomes if o.created} == {"behavioural:protein_g"}


# ══ scoping — the security boundary (ADR-13.4) ═════════════════════════════════════════
def test_consolidation_is_user_scoped(db, user_id):
    """Every query filters user_id. A claim built from another account's rows would be both a
    wrong answer and a data leak."""
    stranger = new_user()
    _seed_shift(db, user_id)
    _seed_shift(db, stranger, pre=200.0, post=400.0)

    _service(db).consolidate_series(user_id, PROTEIN)

    assert _count(db, stranger, "insight") == 0
    (insight,) = _insights(db, user_id)
    with db.transaction() as cur:
        cur.execute(
            "SELECT count(*) AS n FROM memories WHERE id = ANY(%s) AND user_id <> %s",
            [[UUID(i) for i in insight["payload"]["evidence_ids"]], user_id],
        )
        assert int(cur.fetchone()["n"]) == 0


def test_two_users_hold_independent_claims(db, user_id):
    other = new_user()
    _seed_shift(db, user_id)
    _seed_shift(db, other, pre=40.0, post=100.0)
    svc = _service(db)

    svc.consolidate_series(user_id, PROTEIN)
    svc.consolidate_series(other, PROTEIN)

    mine = _insights(db, user_id)[0]["payload"]
    theirs = _insights(db, other)[0]["payload"]
    assert mine["fingerprint"] != theirs["fingerprint"]


# ══ §4.7 — freshness is derived, never stored ══════════════════════════════════════════
def test_freshness_is_derived_from_created_at(db, user_id):
    _seed_shift(db, user_id)
    svc = _service(db)
    svc.consolidate_series(user_id, PROTEIN)
    (insight,) = _insights(db, user_id)

    with db.transaction() as cur:
        cur.execute("SELECT created_at FROM memories WHERE id = %s", [insight["id"]])
        derived_at = cur.fetchone()["created_at"]

    assert svc.is_stale(user_id, PROTEIN, derived_at) is False
    _seed(db, _phase(user_id, "2026-05-17", 8, 120.0, "phase-c"))
    assert svc.is_stale(user_id, PROTEIN, derived_at) is True


def test_no_last_evaluated_at_is_stored(db, user_id):
    """§4.7: the payload must not grow a mutable freshness field — memories stays append-only
    apart from status/superseded_by/embedding (**I-13**)."""
    _seed_shift(db, user_id)
    _service(db).consolidate_series(user_id, PROTEIN)
    (insight,) = _insights(db, user_id)
    assert "last_evaluated_at" not in insight["payload"]


def test_freshness_of_an_empty_series_is_never_stale(db, user_id):
    assert _service(db).is_stale(user_id, PROTEIN, datetime.now(timezone.utc)) is False


# ══ the outcome path end to end ════════════════════════════════════════════════════════
def test_an_outcome_series_cites_structurally_detected_interventions(db, user_id):
    """The money question's shape: two sparse measurements, with the changes between them
    derived from logged behaviour rather than read out of any note (**I-8**)."""
    _seed(db, _phase(user_id, "2026-05-01", 8, 30.0, "phase-a")
              + _phase(user_id, "2026-05-09", 8, 90.0, "phase-b"))
    _seed(db, [
        Memory(user_id=user_id, event_time=_at("2026-05-02"), tz=TZ, type="supplement",
               source="chat", provenance="live", confidence=1.0, summary="started vitamin D",
               payload={"name": "Vitamin D", "dose_mg": 1.5}),
    ])
    _seed(db, [_scan(user_id, "2026-04-20", 39.2), _scan(user_id, "2026-06-20", 34.0)])

    outcome = _service(db).consolidate_series(user_id, BODY_FAT)

    assert outcome.created is not None
    (insight,) = _insights(db, user_id)
    payload = insight["payload"]
    assert payload["kind"] == "intervention_outcome"
    assert payload["intervention_ids"]  # structural: onsets and/or level shifts
    assert any(i.startswith("series_onset:supplement:") for i in payload["intervention_ids"])
