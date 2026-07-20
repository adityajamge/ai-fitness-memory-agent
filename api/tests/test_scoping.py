"""Per-user scoping is a SECURITY boundary (ADR-13.4): user A can never read user B's
memories. This test is the enforcement the docs promise (04-database-design.md security
notes; 12-test-plan.md scoping path). Same TestClient, sequential sessions — signing up B
replaces the session cookie, so the second GET runs as B against A's memory id."""

from __future__ import annotations

from api.tests.conftest import unique_email


def test_user_cannot_read_another_users_memory(client):
    # User A signs up and logs a meal.
    client.post("/api/auth/signup", json={"email": unique_email(), "password": "hunter2secret"})
    receipt = client.post("/api/ingest", json={"text": "250g curd, 3 eggs, 200g chicken"}).json()
    assert receipt["parse_status"] == "ok"
    memory_id = receipt["created"][0]["id"]

    # A can read their own memory.
    own = client.get(f"/api/memories/{memory_id}")
    assert own.status_code == 200
    assert own.json()["type"] == "meal"

    # User B signs up (cookie now belongs to B) and tries to read A's memory.
    client.post("/api/auth/signup", json={"email": unique_email(), "password": "hunter2secret"})
    cross = client.get(f"/api/memories/{memory_id}")
    assert cross.status_code == 404  # indistinguishable from "not found" — no cross-user leak


def test_ingest_requires_auth(client):
    client.post("/api/auth/logout")
    resp = client.post("/api/ingest", json={"text": "250g curd"})
    assert resp.status_code == 401
