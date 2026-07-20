"""Auth flow tests (T9): signup, login, session, logout."""

from __future__ import annotations

import uuid

from api.deps import SESSION_COOKIE
from api.tests.conftest import unique_email


def test_signup_sets_session_and_returns_user(client):
    email = unique_email()
    resp = client.post("/api/auth/signup", json={"email": email, "password": "hunter2secret"})
    assert resp.status_code == 200
    assert resp.json()["email"] == email
    assert uuid.UUID(resp.json()["user_id"])
    assert SESSION_COOKIE in resp.cookies


def test_duplicate_signup_conflicts(client):
    email = unique_email()
    body = {"email": email, "password": "hunter2secret"}
    assert client.post("/api/auth/signup", json=body).status_code == 200
    assert client.post("/api/auth/signup", json=body).status_code == 409


def test_short_password_rejected(client):
    resp = client.post("/api/auth/signup", json={"email": unique_email(), "password": "short"})
    assert resp.status_code == 422


def test_login_wrong_password_unauthorized(client):
    email = unique_email()
    client.post("/api/auth/signup", json={"email": email, "password": "hunter2secret"})
    client.post("/api/auth/logout")
    resp = client.post("/api/auth/login", json={"email": email, "password": "wrongpassword"})
    assert resp.status_code == 401


def test_protected_route_requires_auth(client):
    # No session cookie yet -> 401.
    resp = client.get(f"/api/memories/{uuid.uuid4()}")
    assert resp.status_code == 401


def test_authenticated_access_then_logout(client):
    email = unique_email()
    client.post("/api/auth/signup", json={"email": email, "password": "hunter2secret"})
    # Authenticated: a missing memory is 404 (auth passed), not 401.
    assert client.get(f"/api/memories/{uuid.uuid4()}").status_code == 404
    # After logout the same route is 401 again.
    client.post("/api/auth/logout")
    assert client.get(f"/api/memories/{uuid.uuid4()}").status_code == 401
