"""Photo ingestion tests (M7, ephemeral-storage variant — TODOS.md, consolidation-architecture.md
§4.17). Mirrors test_ingestion.py's shape: ``ingest_photo`` shares stages B onward with
``ingest_text`` via ``_build_memories``/``_persist_validated``, so these tests focus on what's
actually new — stage A (vision) and its note fallback — not re-proving the shared stages.
"""

from __future__ import annotations

from datetime import datetime, timezone

from engine.ingestion import IngestionService
from engine.model import ExtractedEvent
from engine.tests.conftest import FakeModelProvider

_FAKE_JPEG = b"\xff\xd8\xff\xe0not-a-real-jpeg-but-fine-for-a-fake-provider"


def _service(db, provider, **kw) -> IngestionService:
    return IngestionService(db, provider, default_tz="Asia/Kolkata", **kw)


def _fetch(db, user_id, memory_id):
    from engine.repository import get_memory

    with db.transaction() as cur:
        return get_memory(cur, user_id, memory_id)


def _vision_meal_event() -> ExtractedEvent:
    """A photo-derived meal: chicken with a caption-stated quantity (stated), rice with only
    a visual estimate (ai_estimated) — the qty_g-only-when-stated rule in one fixture."""
    return ExtractedEvent(
        type="meal",
        event_time=datetime(2026, 8, 14, 20, 0, tzinfo=timezone.utc),
        tz="Asia/Kolkata",
        confidence=0.85,
        summary="dinner: 200g chicken breast, rice",
        payload={
            "meal_type": "dinner",
            "items": [
                {"name": "chicken breast", "qty_g": 200},  # caption said "200g"
                {"name": "rice", "qty_text": "approximately 150g, visual estimate"},
            ],
        },
    )


def test_photo_success_routes_to_typed_meal(db, user_id):
    provider = FakeModelProvider(vision_events=[_vision_meal_event()])
    svc = _service(db, provider)

    receipt = svc.ingest_photo(
        user_id, _FAKE_JPEG, "image/jpeg", caption="200g chicken breast, log this for dinner"
    )

    assert receipt.parse_status == "ok"
    assert len(receipt.created) == 1
    ref = receipt.created[0]
    assert ref.type == "meal"

    row = _fetch(db, user_id, ref.id)
    assert row["source"] == "photo_upload"
    assert row["provenance"] == "live"
    items = row["payload"]["items"]
    chicken, rice = items[0], items[1]
    assert chicken["qty_g"] == 200  # stated, from the caption
    assert rice.get("qty_g") is None  # never a vision guess masquerading as stated
    assert rice["qty_text"] == "approximately 150g, visual estimate"

    # The provider actually saw the bytes/caption we sent.
    assert provider.vision_calls == 1
    assert provider.last_image_bytes == _FAKE_JPEG
    assert provider.last_mime_type == "image/jpeg"
    assert provider.last_vision_caption == "200g chicken breast, log this for dinner"


def test_photo_no_food_is_a_noop_not_a_note(db, user_id):
    """Mirrors test_contentless_turn_is_a_noop_not_a_note: an affirmed-empty vision result
    (photo has no identifiable food) writes nothing."""
    provider = FakeModelProvider(vision_events=[])  # explicit [] == affirmed no food
    svc = _service(db, provider)

    receipt = svc.ingest_photo(user_id, _FAKE_JPEG, "image/jpeg", caption="")

    assert receipt.parse_status == "ok"
    assert receipt.message == "nothing to log"
    assert receipt.created == []


def test_photo_vision_failure_persists_note_with_caption(db, user_id):
    """VisionError -> note fallback, same never-lose-input posture as a failed text
    extraction. The caption survives even though the photo itself does not (M7's
    ephemeral-storage tradeoff)."""
    provider = FakeModelProvider(vision_error=True)
    svc = _service(db, provider)

    receipt = svc.ingest_photo(
        user_id, _FAKE_JPEG, "image/jpeg", caption="some kind of curry, not sure what's in it"
    )

    assert receipt.parse_status == "incomplete"
    assert receipt.message == "saved — parsing incomplete"
    row = _fetch(db, user_id, receipt.created[0].id)
    assert row["type"] == "note"
    assert row["source"] == "photo_upload"
    assert row["payload"]["text"] == "some kind of curry, not sure what's in it"


def test_photo_vision_failure_without_caption_uses_honest_placeholder(db, user_id):
    """No caption at all -> the literal '[photo, not parsed]', never an invented
    description of an image nobody could read."""
    provider = FakeModelProvider(vision_error=True)
    svc = _service(db, provider)

    receipt = svc.ingest_photo(user_id, _FAKE_JPEG, "image/jpeg", caption="")

    row = _fetch(db, user_id, receipt.created[0].id)
    assert row["type"] == "note"
    assert row["payload"]["text"] == "[photo, not parsed]"


def test_photo_validation_failure_falls_back_to_note(db, user_id):
    """A hot field of the wrong type from vision is an all-or-nothing validation failure,
    same as text extraction (test_validation_failure_falls_back_to_note)."""
    bad = ExtractedEvent(
        type="body_scan",
        event_time=datetime(2026, 8, 14, 7, 0, tzinfo=timezone.utc),
        tz="Asia/Kolkata",
        confidence=0.7,
        summary="a scale in the photo",
        payload={"body_fat_pct": "not-a-number"},
    )
    provider = FakeModelProvider(vision_events=[bad])
    svc = _service(db, provider)

    receipt = svc.ingest_photo(user_id, _FAKE_JPEG, "image/jpeg", caption="")

    assert receipt.parse_status == "incomplete"
    row = _fetch(db, user_id, receipt.created[0].id)
    assert row["type"] == "note"
    assert row["payload"]["text"] == "[photo, not parsed]"
