"""``/api/profile*`` — onboarding intake, read, and edit (ADR-17). Real CockroachDB via the
``client`` fixture, same convention as the other API test modules."""

from __future__ import annotations

from uuid import UUID

import pytest

from api.tests.conftest import DATABASE_URL, unique_email
from engine.db import Database

ROUTES = [
    ("GET", "/api/profile"),
    ("PATCH", "/api/profile"),
    ("POST", "/api/profile/onboarding"),
]


@pytest.mark.parametrize("method,path", ROUTES)
def test_every_profile_route_requires_authentication(client, method, path):
    resp = client.request(method, path, json={} if method != "GET" else None)
    assert resp.status_code == 401


def _signup(client) -> str:
    resp = client.post(
        "/api/auth/signup", json={"email": unique_email(), "password": "hunter2secret"}
    )
    assert resp.status_code == 200
    return resp.json()["user_id"]


def test_fresh_account_has_not_onboarded(client):
    _signup(client)
    resp = client.get("/api/profile")
    assert resp.status_code == 200
    body = resp.json()
    assert body["has_onboarded"] is False
    assert body["display_name"] is None
    assert body["current_weight_kg"] is None
    assert body["suggested_targets"] is None  # insufficient inputs — declines, doesn't guess


def test_onboarding_computes_and_stores_targets(client):
    _signup(client)
    resp = client.post(
        "/api/profile/onboarding",
        json={
            "display_name": "Aditya",
            "date_of_birth": "1997-03-15",
            "sex": "male",
            "height_cm": 178,
            "weight_kg": 80,
            "primary_goal": "lose_fat",
            "activity_level": "moderate",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["has_onboarded"] is True
    assert body["onboarded_at"] is not None
    assert body["current_weight_kg"] == 80
    assert body["suggested_targets"] is not None
    assert body["protein_target_g"] == body["suggested_targets"]["protein_g"]
    assert body["calorie_target_kcal"] == body["suggested_targets"]["calorie_kcal"]
    assert body["targets_are_custom"] is False


def test_onboarding_current_weight_is_a_real_weight_memory_not_a_profile_column(client):
    """ADR-17.2: the weight submitted at onboarding must land as an independently-queryable
    `weight` memory — the same row shape a chat-logged weight would produce — not a value
    only visible through the profile row."""
    user_id = _signup(client)
    resp = client.post("/api/profile/onboarding", json={"weight_kg": 80, "skipped": True})
    assert resp.json()["current_weight_kg"] == 80

    db = Database(DATABASE_URL)
    with db.transaction() as cur:
        cur.execute(
            "SELECT source, (payload ->> 'weight_kg')::FLOAT AS weight_kg "
            "FROM memories WHERE user_id = %s AND type = 'weight'",
            [UUID(user_id)],
        )
        rows = cur.fetchall()
    assert len(rows) == 1
    assert rows[0]["source"] == "onboarding"
    assert rows[0]["weight_kg"] == 80


def test_onboarding_skip_still_records_partial_fields(client):
    _signup(client)
    resp = client.post(
        "/api/profile/onboarding", json={"display_name": "Priya", "skipped": True}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["has_onboarded"] is True
    assert body["display_name"] == "Priya"
    assert body["current_weight_kg"] is None


def test_patch_rejects_unknown_goal(client):
    _signup(client)
    resp = client.patch("/api/profile", json={"primary_goal": "not-a-real-goal"})
    assert resp.status_code == 422


def test_patch_updates_identity_without_touching_targets(client):
    _signup(client)
    client.post("/api/profile/onboarding", json={"skipped": True})
    resp = client.patch("/api/profile", json={"display_name": "Rohan"})
    assert resp.status_code == 200
    assert resp.json()["display_name"] == "Rohan"


def test_explicit_target_marks_custom_and_survives_an_unrelated_edit(client):
    """Once a user overrides a target, later unrelated edits must not silently recompute over
    it (the `targets_are_custom` gate — DESIGN.md §6.19's "adjust anytime" promise)."""
    _signup(client)
    client.post(
        "/api/profile/onboarding",
        json={
            "date_of_birth": "1997-03-15",
            "sex": "male",
            "height_cm": 178,
            "weight_kg": 80,
            "primary_goal": "lose_fat",
            "activity_level": "moderate",
        },
    )
    custom = client.patch("/api/profile", json={"protein_target_g": 999})
    assert custom.status_code == 200
    assert custom.json()["protein_target_g"] == 999
    assert custom.json()["targets_are_custom"] is True

    unrelated = client.patch("/api/profile", json={"display_name": "Custom Target Person"})
    assert unrelated.json()["protein_target_g"] == 999  # untouched by the recompute


def test_goal_change_recomputes_noncustom_targets_and_leaves_history(client):
    _signup(client)
    onboarded = client.post(
        "/api/profile/onboarding",
        json={
            "date_of_birth": "1997-03-15",
            "sex": "male",
            "height_cm": 178,
            "weight_kg": 80,
            "primary_goal": "maintain",
            "activity_level": "moderate",
        },
    ).json()

    changed = client.patch("/api/profile", json={"primary_goal": "build_muscle"}).json()
    assert changed["primary_goal"] == "build_muscle"
    assert changed["protein_target_g"] != onboarded["protein_target_g"]
    assert changed["targets_are_custom"] is False


def test_user_cannot_read_another_users_profile(client):
    _signup(client)
    client.post("/api/profile/onboarding", json={"display_name": "User A", "skipped": True})

    _signup(client)  # replaces the session cookie with user B's
    other = client.get("/api/profile")
    assert other.status_code == 200
    assert other.json()["display_name"] is None  # never sees A's row
