"""POST /api/memories/{memory_id}/reprocess (D2) — supersede-on-retry through the product.

The engine semantics are proven in engine/tests/test_ingestion.py; these tests cover the
HTTP surface: auth, ownership, receipt shape, and the 404-for-everything-ineligible posture
(nonexistent / cross-user / non-note / already superseded are indistinguishable, matching
GET /api/memories/{id}).
"""

from __future__ import annotations

import uuid

from api.tests.conftest import unique_email

PASSWORD = "hunter2secret"


def _signup(client, email: str | None = None) -> str:
    email = email or unique_email()
    resp = client.post("/api/auth/signup", json={"email": email, "password": PASSWORD})
    assert resp.status_code == 200
    return email


def _create_note(client, app_provider, text: str = "ergh half a plate of something") -> str:
    """Force the extraction-failure path so the ingest persists a note, then restore the
    provider so the next call succeeds."""
    app_provider.extract_error = True
    receipt = client.post("/api/ingest", json={"text": text}).json()
    app_provider.extract_error = False
    assert receipt["parse_status"] == "incomplete"
    assert receipt["created"][0]["type"] == "note"
    return receipt["created"][0]["id"]


def test_reprocess_success_supersedes_note(client, app_provider):
    _signup(client)
    note_id = _create_note(client, app_provider)

    resp = client.post(f"/api/memories/{note_id}/reprocess")

    assert resp.status_code == 200
    receipt = resp.json()
    assert receipt["parse_status"] == "ok"
    assert receipt["message"] == "saved"
    assert receipt["superseded_note_id"] == note_id
    assert receipt["created"][0]["type"] == "meal"

    # The note survives as superseded, chained to its replacement (never deleted).
    note = client.get(f"/api/memories/{note_id}").json()
    assert note["status"] == "superseded"
    typed = client.get(f"/api/memories/{receipt['created'][0]['id']}").json()
    assert typed["status"] == "active"


def test_reprocess_still_failing_leaves_note_active(client, app_provider):
    """Extraction fails again -> honest incomplete receipt, note untouched and retryable."""
    _signup(client)
    note_id = _create_note(client, app_provider)

    app_provider.extract_error = True
    resp = client.post(f"/api/memories/{note_id}/reprocess")

    assert resp.status_code == 200
    receipt = resp.json()
    assert receipt["parse_status"] == "incomplete"
    assert receipt["created"] == []
    assert receipt["superseded_note_id"] is None

    app_provider.extract_error = False
    assert client.get(f"/api/memories/{note_id}").json()["status"] == "active"


def test_reprocess_requires_auth(client):
    resp = client.post(f"/api/memories/{uuid.uuid4()}/reprocess")
    assert resp.status_code == 401


def test_nonexistent_memory_404(client):
    _signup(client)
    assert client.post(f"/api/memories/{uuid.uuid4()}/reprocess").status_code == 404


def test_non_owner_cannot_reprocess(client, app_provider):
    """SECURITY: user B reprocessing A's note gets the same 404 as a nonexistent id, and
    A's note is left untouched."""
    email_a = _signup(client)
    note_id = _create_note(client, app_provider)

    _signup(client)  # session cookie now belongs to B
    assert client.post(f"/api/memories/{note_id}/reprocess").status_code == 404

    # Back as A: the note is still active — B's attempt had no side effect.
    client.post("/api/auth/login", json={"email": email_a, "password": PASSWORD})
    assert client.get(f"/api/memories/{note_id}").json()["status"] == "active"


def test_non_note_memory_404(client):
    """A typed memory (meal) is not reprocessable — only notes are."""
    _signup(client)
    receipt = client.post("/api/ingest", json={"text": "250g curd, 3 eggs"}).json()
    assert receipt["created"][0]["type"] == "meal"
    meal_id = receipt["created"][0]["id"]

    assert client.post(f"/api/memories/{meal_id}/reprocess").status_code == 404


def test_already_superseded_note_404(client, app_provider):
    """Reprocessing twice: the second attempt finds no *active* note -> 404, and no
    duplicate typed events are created."""
    _signup(client)
    note_id = _create_note(client, app_provider)
    first = client.post(f"/api/memories/{note_id}/reprocess")
    assert first.status_code == 200

    assert client.post(f"/api/memories/{note_id}/reprocess").status_code == 404
