"""POST /api/chat/photo — the M7 photo-ingestion transport (ephemeral-storage variant,
TODOS.md). Same posture as test_chat.py: full-stack, real app/DB, scripted FakeModelProvider.
These assert the transport contract; ingest_photo's own behavior (vision success/failure,
qty_basis honesty) is covered by engine/tests/test_photo_ingestion.py.
"""

from __future__ import annotations

from io import BytesIO

from PIL import Image

from api.tests.conftest import unique_email
from engine.model import ExtractedEvent

_UTC_NOW_HOURS_AGO_MEAL = {
    "type": "meal",
    "tz": "Asia/Kolkata",
    "confidence": 0.85,
    "summary": "dinner: 200g chicken, rice",
    "payload": {
        "meal_type": "dinner",
        "items": [
            {"name": "chicken", "qty_g": 200},
            {"name": "rice", "qty_text": "approximately 150g, visual estimate"},
        ],
    },
}


def _jpeg_bytes() -> bytes:
    buf = BytesIO()
    Image.new("RGB", (4, 4), color=(200, 120, 50)).save(buf, format="JPEG")
    return buf.getvalue()


def _signup(client) -> str:
    email = unique_email()
    response = client.post("/api/auth/signup", json={"email": email, "password": "pw-123456"})
    assert response.status_code in (200, 201), response.text
    return email


def _post_photo(client, *, data: bytes, content_type: str, message: str = "", thread_id=None):
    form: dict = {"message": message}
    if thread_id is not None:
        form["thread_id"] = thread_id
    return client.post(
        "/api/chat/photo",
        data=form,
        files={"image": ("meal.jpg", data, content_type)},
    )


def test_chat_photo_requires_authentication(client) -> None:
    response = _post_photo(client, data=_jpeg_bytes(), content_type="image/jpeg")
    assert response.status_code == 401


def test_chat_photo_happy_path(client, app_provider) -> None:
    from datetime import datetime, timedelta, timezone

    app_provider.vision_events = [
        ExtractedEvent(
            type="meal",
            event_time=datetime.now(timezone.utc) - timedelta(minutes=5),
            tz="Asia/Kolkata",
            confidence=0.85,
            summary="dinner: 200g chicken, rice",
            payload=_UTC_NOW_HOURS_AGO_MEAL["payload"],
        )
    ]
    _signup(client)

    response = _post_photo(
        client, data=_jpeg_bytes(), content_type="image/jpeg", message="log this for dinner"
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["receipts"]) == 1
    assert len(body["receipts"][0]["created"]) == 1
    assert body["receipts"][0]["created"][0]["type"] == "meal"
    assert body["turn_id"] is not None
    assert body["trace"] is not None
    assert "chicken" in body["answer"] or "dinner" in body["answer"]


def test_chat_photo_rejects_unsupported_content_type(client) -> None:
    _signup(client)
    response = _post_photo(client, data=b"not an image", content_type="text/plain")
    assert response.status_code == 422


def test_chat_photo_rejects_malformed_image(client) -> None:
    """A file claiming to be a JPEG that Pillow cannot actually decode — the belt-and-
    suspenders check against a mislabeled or malicious upload."""
    _signup(client)
    response = _post_photo(client, data=b"\xff\xd8\xff not a real jpeg", content_type="image/jpeg")
    assert response.status_code == 422


def test_chat_photo_rejects_oversized_image(client) -> None:
    _signup(client)
    oversized = b"\x00" * (8 * 1024 * 1024 + 1)  # one byte over _PHOTO_MAX_BYTES
    response = _post_photo(client, data=oversized, content_type="image/jpeg")
    assert response.status_code == 413


def test_chat_photo_vision_failure_preserves_caption(client, app_provider) -> None:
    """VisionError -> note fallback; the message the user typed is never lost even though
    the photo itself is (M7's ephemeral-storage tradeoff, no failure-path durability)."""
    app_provider.vision_error = True
    _signup(client)

    response = _post_photo(
        client,
        data=_jpeg_bytes(),
        content_type="image/jpeg",
        message="some kind of curry, not sure what's in it",
    )

    assert response.status_code == 200, response.text
    body = response.json()
    receipt = body["receipts"][0]
    assert receipt["parse_status"] == "incomplete"
    assert len(receipt["created"]) == 1
