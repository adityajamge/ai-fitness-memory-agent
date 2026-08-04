"""Stage (F₀) — consolidation riding the ingestion tail (Phase 5 M5a, §4.8).

The write path is the most load-bearing code in the project: 640 tests stand on its
guarantees, and this milestone adds a stage to it. So these tests are mostly about what
(F₀) must *not* do.

* **I-14** — it runs outside the turn's write transaction. Asserted by proving no transaction
  is open while it runs, not by reading the code.
* **I-15** — a consolidation failure never fails the turn. The memories still commit, the
  receipt is still correct, and the user never learns that a hypothesis fell over.
* **never-lose-input** — unchanged. (F₀) sits after the commit, so it cannot roll anything back.
* The shared tail is **not forked**: ``ingest_text`` and ``ingest_events`` reach the same stage.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest

from engine.consolidation import ConsolidationService, series_touched_by
from engine.ingestion import IngestionService
from engine.memory import Memory
from engine.model import ExtractedEvent
from engine.tests.conftest import FakeModelProvider

TZ = "Asia/Kolkata"
IST = timezone(timedelta(hours=5, minutes=30))


def _at(day: str) -> datetime:
    return datetime.fromisoformat(f"{day}T12:00:00").replace(tzinfo=IST)


def _meal_event(day: str, protein: float, composition: str | None = None) -> ExtractedEvent:
    payload: dict = {"nutrition": {"protein_g": protein}, "items": []}
    if composition:
        payload["expanded_from"] = {"composition": composition, "assertion": f"{protein:g} g/day"}
    return ExtractedEvent(
        type="meal", event_time=_at(day), tz=TZ, confidence=1.0,
        summary=f"meal {protein:g}g", payload=payload,
    )


def _phase(start: str, days: int, protein: float, composition: str) -> list[ExtractedEvent]:
    first = datetime.fromisoformat(start).date()
    return [
        _meal_event((first + timedelta(days=n)).isoformat(), protein, composition)
        for n in range(days)
    ]


def _service(db, *, consolidation=None, events=None) -> IngestionService:
    return IngestionService(
        db, FakeModelProvider(events or []), default_tz=TZ, consolidation=consolidation
    )


def _consolidator(db, **kwargs) -> ConsolidationService:
    return ConsolidationService(db, default_tz=TZ, budget_ms=60_000, **kwargs)


def _count(db, user_id: UUID, memory_type: str) -> int:
    with db.transaction() as cur:
        cur.execute(
            "SELECT count(*) AS n FROM memories WHERE user_id = %s AND type = %s",
            [user_id, memory_type],
        )
        return int(cur.fetchone()["n"])


def _seed_history(db, user_id: UUID) -> None:
    """Put a shift in the data **without** consolidating, so the next turn is the one that
    derives the claim.

    Seeding through a consolidating service would derive the insight during setup, and the
    turn under test would then correctly report nothing new — which is the whole point of the
    identity rule, but not what these tests are trying to observe."""
    quiet = _service(db)  # no consolidation injected
    quiet.ingest_events(user_id, _phase("2026-05-01", 8, 30.0, "phase-a"), provenance="live")
    quiet.ingest_events(user_id, _phase("2026-05-09", 7, 60.0, "phase-b"), provenance="live")


# ── which series a turn touches (§4.4) ─────────────────────────────────────────────────
def test_a_meal_touches_exactly_one_consolidatable_series():
    """The budget's primary defence: a meal payload carries four metrics, one of which is
    consolidatable, so lunch costs one series scan rather than nine."""
    meal = Memory(
        user_id=UUID(int=1), event_time=_at("2026-05-01"), tz=TZ, type="meal", source="chat",
        provenance="live", confidence=1.0,
        payload={"nutrition": {"protein_g": 40, "carbs_g": 60, "fat_g": 20, "kcal": 600}},
    )
    assert [str(k) for k in series_touched_by([meal])] == ["behavioural:protein_g"]


def test_a_payload_without_the_metric_touches_nothing():
    """A meal logged with no macros triggers no work at all."""
    meal = Memory(
        user_id=UUID(int=1), event_time=_at("2026-05-01"), tz=TZ, type="meal", source="chat",
        provenance="live", confidence=1.0, payload={"items": [{"name": "toast"}]},
    )
    assert series_touched_by([meal]) == []


def test_touched_series_are_deduplicated_and_ordered():
    """A body scan touches body_fat_pct only. Its `weight_kg` reading belongs to the
    `body_scan_weight_kg` metric, which is aggregatable but deliberately not consolidatable —
    `weight_kg` is typed to the standalone `weight` memory type."""
    scan = Memory(
        user_id=UUID(int=1), event_time=_at("2026-05-01"), tz=TZ, type="body_scan",
        source="chat", provenance="live", confidence=1.0,
        payload={"body_fat_pct": 30.0, "weight_kg": 70.0},
    )
    assert [str(k) for k in series_touched_by([scan, scan])] == ["outcome:body_fat_pct"]


# ── the hook itself ────────────────────────────────────────────────────────────────────
def test_an_ingest_turn_derives_an_insight_and_reports_it_in_the_receipt(db, user_id):
    """The live demo beat, at the service layer: logging tips a series over and the receipt
    that comes back says so in the same turn."""
    svc = _service(db, consolidation=_consolidator(db))
    _seed_history(db, user_id)

    receipt = svc.ingest_events(user_id, [_meal_event("2026-05-16", 60.0, "phase-b")],
                                provenance="live")

    assert receipt.parse_status == "ok"
    assert len(receipt.created) == 1
    assert len(receipt.insights) == 1
    assert receipt.insights[0].type == "insight"
    assert receipt.insights[0].summary
    # I-16 — written unembedded, so no Bedrock call rode the ingest path.
    assert receipt.insights[0].embedding_pending is True


def test_the_receipt_keeps_the_two_tiers_apart(db, user_id):
    """`created` is what the user reported; `insights` is what the engine claimed about it.
    Merging them would let a receipt imply the user logged something they never said."""
    svc = _service(db, consolidation=_consolidator(db))
    _seed_history(db, user_id)

    receipt = svc.ingest_events(user_id, [_meal_event("2026-05-16", 60.0, "phase-b")],
                                provenance="live")

    assert all(ref.type == "meal" for ref in receipt.created)
    assert all(ref.type == "insight" for ref in receipt.insights)


def test_a_turn_that_touches_nothing_consolidatable_writes_no_insight(db, user_id):
    svc = _service(db, consolidation=_consolidator(db))
    receipt = svc.ingest_events(user_id, [ExtractedEvent(
        type="note", event_time=_at("2026-05-01"), tz=TZ, confidence=1.0,
        summary="just a thought", payload={"text": "just a thought"},
    )], provenance="live")

    assert receipt.insights == []
    assert _count(db, user_id, "insight") == 0


def test_ingest_text_reaches_the_same_stage(db, user_id):
    """The shared (B)–(F) tail is not forked: both entry points get (F₀)."""
    svc = _service(db, consolidation=_consolidator(db))
    _seed_history(db, user_id)

    svc.model.events = [_meal_event("2026-05-16", 60.0, "phase-b")]
    receipt = svc.ingest_text(user_id, "another day of the same")

    assert len(receipt.insights) == 1


# ── I-15: a failure here never fails the turn ──────────────────────────────────────────
class _ExplodingConsolidator(ConsolidationService):
    def consolidate_touched(self, *args, **kwargs):  # noqa: D102
        raise RuntimeError("consolidation blew up")


def test_a_consolidation_failure_never_fails_the_turn(db, user_id, caplog):
    """**I-15.** The user's meal is committed and the receipt is honest; the hypothesis that
    fell over costs one re-derivation on the next ingest, and nothing else."""
    svc = _service(db, consolidation=_ExplodingConsolidator(db, default_tz=TZ))

    receipt = svc.ingest_events(user_id, [_meal_event("2026-05-01", 40.0)], provenance="live")

    assert receipt.parse_status == "ok"
    assert len(receipt.created) == 1
    assert receipt.insights == []
    assert _count(db, user_id, "meal") == 1  # committed, not rolled back


def test_a_consolidation_failure_leaves_the_memories_committed(db, user_id):
    """never-lose-input is untouched by (F₀): it sits *after* the commit, so a derived-data
    failure structurally cannot roll back a fact the user reported."""
    svc = _service(db, consolidation=_ExplodingConsolidator(db, default_tz=TZ))
    svc.ingest_events(user_id, _phase("2026-05-01", 3, 40.0, "phase-a"), provenance="live")
    assert _count(db, user_id, "meal") == 3


# ── I-14: outside the turn's transaction ───────────────────────────────────────────────
class _TransactionSpy(ConsolidationService):
    """Records whether the ingestion transaction is still open when (F₀) runs."""

    saw_open_transaction: bool | None = None

    def consolidate_touched(self, user_id, memories, **kwargs):
        # A fresh connection can only be opened and used if we are *not* nested inside an
        # in-flight write transaction on the same connection; more directly, the memories are
        # already visible to a NEW connection, which is only true post-commit.
        with self.db.transaction() as cur:
            cur.execute(
                "SELECT count(*) AS n FROM memories WHERE user_id = %s AND type = 'meal'",
                [user_id],
            )
            self.saw_open_transaction = int(cur.fetchone()["n"]) == 0
        return super().consolidate_touched(user_id, memories, **kwargs)


def test_consolidation_runs_after_the_turn_has_committed(db, user_id):
    """**I-14.** If (F₀) ran inside the write transaction, an independent connection could not
    yet see the rows. It can — so the commit has already happened."""
    spy = _TransactionSpy(db, default_tz=TZ, budget_ms=60_000)
    svc = _service(db, consolidation=spy)

    svc.ingest_events(user_id, _phase("2026-05-01", 3, 40.0, "phase-a"), provenance="live")

    assert spy.saw_open_transaction is False, "memories must be visible to another connection"


def test_no_model_call_is_made_by_consolidation(db, user_id):
    """The (F₀) stage adds no Bedrock round trip: embed calls stay at the count the write path
    itself makes (I-16, and the reason the budget is reachable at all)."""
    svc = _service(db, consolidation=_consolidator(db))
    _seed_history(db, user_id)
    before = svc.model.embed_calls

    receipt = svc.ingest_events(user_id, [_meal_event("2026-05-16", 60.0, "phase-b")],
                                provenance="live")

    assert receipt.insights  # consolidation definitely ran
    # Only the write path's own embed (stage C) plus its backfill sweep; consolidation adds none.
    assert svc.model.embed_calls - before <= 2


# ── the service still stands alone ─────────────────────────────────────────────────────
def test_ingestion_without_a_consolidator_behaves_exactly_as_before(db, user_id):
    """Phase 2's write path is unchanged for every caller that does not opt in — which is what
    keeps 600+ pre-existing tests meaningful."""
    svc = _service(db)  # no consolidation injected
    receipt = svc.ingest_events(user_id, [_meal_event("2026-05-01", 40.0)], provenance="live")

    assert receipt.parse_status == "ok"
    assert receipt.insights == []
    assert _count(db, user_id, "insight") == 0


def test_repeated_ingests_do_not_multiply_insights(db, user_id):
    """**I-12 through the hook**, and the reason ``claim_dates`` exists.

    Consolidation fires on every touching ingest. Each further day at the same level extends
    the *evidence*, not the *claim* — so after the first derivation nothing more is written,
    and the table holds exactly one row rather than one per lunch."""
    svc = _service(db, consolidation=_consolidator(db))
    _seed_history(db, user_id)

    first = svc.ingest_events(user_id, [_meal_event("2026-05-16", 60.0, "phase-b")],
                              provenance="live")
    assert len(first.insights) == 1

    for day in ("2026-05-17", "2026-05-18", "2026-05-19"):
        later = svc.ingest_events(user_id, [_meal_event(day, 60.0, "phase-b")], provenance="live")
        assert later.insights == [], "an unchanged claim must not be re-derived"

    assert _count(db, user_id, "insight") == 1  # no duplicates, and no superseded churn


def test_a_genuine_claim_change_still_supersedes(db, user_id):
    """The other half of the same rule: when the level really moves, the claim really changes,
    and the chain records it."""
    svc = _service(db, consolidation=_consolidator(db))
    _seed_history(db, user_id)
    svc.ingest_events(user_id, [_meal_event("2026-05-16", 60.0, "phase-b")], provenance="live")

    changed = svc.ingest_events(user_id, _phase("2026-05-17", 8, 120.0, "phase-c"),
                                provenance="live")

    assert len(changed.insights) == 1
    assert _count(db, user_id, "insight") == 2  # one active, one superseded
    with db.transaction() as cur:
        cur.execute(
            "SELECT count(*) AS n FROM memories WHERE user_id = %s AND type = 'insight' "
            "AND status = 'active'", [user_id],
        )
        assert int(cur.fetchone()["n"]) == 1


@pytest.mark.parametrize("budget", [0])
def test_an_exhausted_budget_still_returns_a_clean_receipt(db, user_id, budget):
    """§4.8: overflow defers, and the turn is undisturbed."""
    svc = _service(db, consolidation=ConsolidationService(
        db, default_tz=TZ, budget_ms=budget, clock=iter([0.0] + [1e9] * 50).__next__
    ))
    receipt = svc.ingest_events(user_id, [_meal_event("2026-05-01", 40.0)], provenance="live")

    assert receipt.parse_status == "ok"
    assert receipt.insights == []
