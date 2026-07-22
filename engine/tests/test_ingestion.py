"""Ingestion write-path tests (T4 + T15) — one per row of the failure matrix in
docs/engineering/ingestion-transaction-boundaries.md §9, plus the happy paths from
12-test-plan.md. Runs against real CockroachDB via the ``db`` fixture.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from engine.ingestion import IngestionService
from engine.model import ExtractedEvent
from engine.repository import get_memory
from engine.tests.conftest import FakeModelProvider


def _meal_event() -> ExtractedEvent:
    return ExtractedEvent(
        type="meal",
        event_time=datetime(2026, 7, 20, 13, 0, tzinfo=timezone.utc),
        tz="Asia/Kolkata",
        confidence=0.9,
        summary="lunch: 250g curd, 3 eggs, 200g chicken",
        payload={
            "meal_type": "lunch",
            "items": [
                {"name": "curd", "qty_g": 250},
                {"name": "eggs", "qty": 3},
                {"name": "chicken", "qty_g": 200},
            ],
            "nutrition": {"protein_g": 78, "kcal": 720, "estimated": True},
        },
    )


def _service(db, provider, **kw) -> IngestionService:
    return IngestionService(db, provider, default_tz="Asia/Kolkata", **kw)


def _fetch(db, user_id, memory_id):
    with db.transaction() as cur:
        return get_memory(cur, user_id, memory_id)


def test_meal_routes_to_typed_event(db, user_id):
    svc = _service(db, FakeModelProvider([_meal_event()]))
    receipt = svc.ingest_text(user_id, "250g curd, 3 eggs, 200g chicken")

    assert receipt.parse_status == "ok"
    assert receipt.message == "saved"
    assert len(receipt.created) == 1
    ref = receipt.created[0]
    assert ref.type == "meal"
    assert ref.embedding_pending is False

    row = _fetch(db, user_id, ref.id)
    assert row["type"] == "meal"
    assert row["provenance"] == "live"
    assert row["confidence"] == pytest.approx(0.9)
    assert row["payload"]["nutrition"]["protein_g"] == 78
    assert row["status"] == "active"
    assert row["has_embedding"] is True


def test_note_type_routes_and_persists(db, user_id):
    note_event = ExtractedEvent(
        type="note",
        event_time=datetime(2026, 7, 20, 9, 0, tzinfo=timezone.utc),
        tz="Asia/Kolkata",
        confidence=1.0,
        summary="knee felt sore on the morning run",
        payload={"text": "knee felt sore on the morning run"},
    )
    svc = _service(db, FakeModelProvider([note_event]))
    receipt = svc.ingest_text(user_id, "knee felt sore on the morning run")
    assert receipt.parse_status == "ok"
    row = _fetch(db, user_id, receipt.created[0].id)
    assert row["type"] == "note"


def test_contentless_turn_is_a_noop_not_a_note(db, user_id):
    """D1: a provider-affirmed contentless turn ("thanks!") writes nothing.

    The counterpart — content the provider could not type — raises ExtractionError and is
    covered by test_extraction_failure_persists_note below. Together they are the whole
    empty-result contract as the engine sees it."""
    svc = _service(db, FakeModelProvider([]))  # empty == affirmed contentless
    receipt = svc.ingest_text(user_id, "thanks!")

    assert receipt.parse_status == "ok"
    assert receipt.message == "nothing to log"
    assert receipt.created == []


def test_extraction_failure_persists_note(db, user_id):
    """16A: extraction fails -> note persists, receipt 'saved — parsing incomplete'."""
    provider = FakeModelProvider(extract_error=True)
    svc = _service(db, provider)
    receipt = svc.ingest_text(user_id, "ergh half a plate of something")

    assert receipt.parse_status == "incomplete"
    assert receipt.message == "saved — parsing incomplete"
    assert provider.extract_calls == 2  # one inline retry before giving up
    assert len(receipt.created) == 1
    row = _fetch(db, user_id, receipt.created[0].id)
    assert row["type"] == "note"
    assert row["payload"]["text"] == "ergh half a plate of something"
    assert row["status"] == "active"


def test_inline_retry_success_writes_typed_event(db, user_id):
    """First extraction attempt fails, retry succeeds -> typed event, no note."""
    provider = FakeModelProvider([_meal_event()], fail_first=True)
    svc = _service(db, provider)
    receipt = svc.ingest_text(user_id, "250g curd, 3 eggs")

    assert receipt.parse_status == "ok"
    assert provider.extract_calls == 2
    assert receipt.created[0].type == "meal"


def test_validation_failure_falls_back_to_note(db, user_id):
    """A hot field of the wrong type is an all-or-nothing validation failure -> note."""
    bad = ExtractedEvent(
        type="body_scan",
        event_time=datetime(2026, 7, 20, 7, 0, tzinfo=timezone.utc),
        tz="Asia/Kolkata",
        confidence=0.8,
        summary="body scan",
        payload={"body_fat_pct": "not-a-number"},
    )
    svc = _service(db, FakeModelProvider([bad]))
    receipt = svc.ingest_text(user_id, "scanned, bf around whatever")
    assert receipt.parse_status == "incomplete"
    row = _fetch(db, user_id, receipt.created[0].id)
    assert row["type"] == "note"


def test_embedding_failure_writes_null_then_backfill(db, user_id):
    """Embedding fails -> NULL embedding row, turn still succeeds; backfill re-embeds (T15)."""
    failing = FakeModelProvider([_meal_event()], embed_error=True)
    svc = _service(db, failing)
    receipt = svc.ingest_text(user_id, "250g curd, 3 eggs, 200g chicken")

    assert receipt.parse_status == "ok"
    assert receipt.created[0].embedding_pending is True
    ref = receipt.created[0]
    assert _fetch(db, user_id, ref.id)["has_embedding"] is False

    # A healthy provider backfills the NULL row.
    good = FakeModelProvider([_meal_event()])
    embedded = _service(db, good).backfill_embeddings(user_id)
    assert embedded == 1
    assert _fetch(db, user_id, ref.id)["has_embedding"] is True


def test_reprocess_note_supersedes_with_typed_events(db, user_id):
    """A failed parse becomes a note; a later successful parse supersedes it (never deletes)."""
    note_receipt = _service(db, FakeModelProvider(extract_error=True)).ingest_text(
        user_id, "250g curd, 3 eggs, 200g chicken"
    )
    note_id = note_receipt.created[0].id

    good = _service(db, FakeModelProvider([_meal_event()]))
    reprocessed = good.reprocess_note(user_id, note_id)

    assert reprocessed.parse_status == "ok"
    assert reprocessed.superseded_note_id == note_id
    assert reprocessed.created[0].type == "meal"

    note_row = _fetch(db, user_id, note_id)
    assert note_row["status"] == "superseded"
    assert note_row["superseded_by"] == reprocessed.created[0].id


def test_reprocess_still_failing_leaves_note_active(db, user_id):
    note_receipt = _service(db, FakeModelProvider(extract_error=True)).ingest_text(
        user_id, "unparseable blob"
    )
    note_id = note_receipt.created[0].id

    still_bad = _service(db, FakeModelProvider(extract_error=True))
    reprocessed = still_bad.reprocess_note(user_id, note_id)

    assert reprocessed.parse_status == "incomplete"
    assert reprocessed.created == []
    assert _fetch(db, user_id, note_id)["status"] == "active"


def test_replay_note_fallback_stays_reconstructed(db, user_id):
    """D3: a replay turn whose extraction fails must persist a *reconstructed* note.

    Mislabelling it 'live' would claim we observed something we actually inferred from old
    records — the exact honesty property ADR-13.10 protects. Guards the T8 replay path
    (03-memory-engine.md §2) before it exists.
    """
    svc = _service(db, FakeModelProvider(extract_error=True))
    receipt = svc.ingest_text(
        user_id,
        "May 12: rough notes, high protein day",
        source="replay",
        provenance="reconstructed",
    )

    assert receipt.parse_status == "incomplete"
    row = _fetch(db, user_id, receipt.created[0].id)
    assert row["type"] == "note"
    assert row["provenance"] == "reconstructed"
    assert row["source"] == "replay"


def test_validation_failure_during_replay_stays_reconstructed(db, user_id):
    """The other note path (stage-B validation failure) must preserve origin too."""
    bad = ExtractedEvent(
        type="body_scan",
        event_time=datetime(2026, 5, 12, 7, 0, tzinfo=timezone.utc),
        tz="Asia/Kolkata",
        confidence=0.4,
        summary="body scan",
        payload={"body_fat_pct": "not-a-number"},
    )
    receipt = _service(db, FakeModelProvider([bad])).ingest_text(
        user_id, "May 12 scan", source="replay", provenance="reconstructed"
    )
    row = _fetch(db, user_id, receipt.created[0].id)
    assert row["type"] == "note"
    assert row["provenance"] == "reconstructed"
    assert row["source"] == "replay"


def test_reprocess_reconstructed_note_yields_reconstructed_memories(db, user_id):
    """D3: upgrading a parse is not a change of origin — the typed events a reconstructed
    note becomes are themselves reconstructed, and the superseded note keeps its origin."""
    note_receipt = _service(db, FakeModelProvider(extract_error=True)).ingest_text(
        user_id, "May 12: 250g curd, 3 eggs", source="replay", provenance="reconstructed"
    )
    note_id = note_receipt.created[0].id

    reprocessed = _service(db, FakeModelProvider([_meal_event()])).reprocess_note(user_id, note_id)
    assert reprocessed.parse_status == "ok"
    assert reprocessed.superseded_note_id == note_id

    typed = _fetch(db, user_id, reprocessed.created[0].id)
    assert typed["type"] == "meal"
    assert typed["provenance"] == "reconstructed"
    assert typed["source"] == "replay"

    note_row = _fetch(db, user_id, note_id)
    assert note_row["status"] == "superseded"
    assert note_row["provenance"] == "reconstructed"


def test_reprocess_live_note_stays_live(db, user_id):
    """The default path is unchanged: a live note upgrades into live typed memories."""
    note_receipt = _service(db, FakeModelProvider(extract_error=True)).ingest_text(
        user_id, "250g curd, 3 eggs"
    )
    reprocessed = _service(db, FakeModelProvider([_meal_event()])).reprocess_note(
        user_id, note_receipt.created[0].id
    )
    typed = _fetch(db, user_id, reprocessed.created[0].id)
    assert typed["provenance"] == "live"
    assert typed["source"] == "chat"


def test_reprocess_is_scoped_to_owner(db, user_id):
    """A note is only reprocessable by its owner — the repository lookup is user-scoped."""
    import uuid

    note_receipt = _service(db, FakeModelProvider(extract_error=True)).ingest_text(
        user_id, "unparseable blob"
    )
    svc = _service(db, FakeModelProvider([_meal_event()]))
    with pytest.raises(ValueError):
        svc.reprocess_note(uuid.uuid4(), note_receipt.created[0].id)
    # Untouched for the real owner.
    assert _fetch(db, user_id, note_receipt.created[0].id)["status"] == "active"


def test_cross_user_get_returns_none(db, user_id):
    """Scoping at the repository layer: another user cannot fetch this user's memory."""
    import uuid

    receipt = _service(db, FakeModelProvider([_meal_event()])).ingest_text(user_id, "lunch")
    other_user = uuid.uuid4()
    assert _fetch(db, other_user, receipt.created[0].id) is None
