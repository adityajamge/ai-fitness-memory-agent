"""Glass-box read API (M3 / T7 · T16).

Full-stack: real FastAPI app, real CockroachDB, real graph, scripted provider. These assert
the *read* contract the UI depends on — that a turn's glass box is fetchable, that the trace
comes back exactly as stored, that hydration is one round trip, and that none of it leaks
across users.

**I-28 is asserted per endpoint, not once.** A single scoping test would prove the pattern is
known, not that every route follows it; a route added later without scoping is exactly the
regression that test would miss.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from agent.tools import AGGREGATE_MEMORIES, LOG_MEMORY
from api.tests.conftest import unique_email
from engine.model import ExtractedEvent, ToolCall

UTC = timezone.utc
NOW = datetime.now(UTC)
RANGE = {
    "start": (NOW - timedelta(days=30)).isoformat(),
    "end": (NOW + timedelta(days=1)).isoformat(),
}

#: Every glass-box route, as (method, path builder). Drives the auth and cross-user sweeps so
#: a new route cannot be added without appearing in both.
ROUTES = [
    ("get", lambda tid: f"/api/turns/{tid}/trace"),
    ("get", lambda tid: "/api/turns"),
    ("get", lambda tid: "/api/stats"),
    ("get", lambda tid: "/api/timeline"),
    ("get", lambda tid: "/api/threads"),
]


def _signup(client) -> str:
    email = unique_email()
    r = client.post("/api/auth/signup", json={"email": email, "password": "pw-123456"})
    assert r.status_code in (200, 201), r.text
    return email


def _logout(client) -> None:
    client.post("/api/auth/logout")


def _meal_event(protein: float = 46.0) -> ExtractedEvent:
    return ExtractedEvent(
        type="meal",
        event_time=NOW - timedelta(hours=1),
        tz="Asia/Kolkata",
        confidence=0.9,
        summary="lunch: 200g chicken",
        payload={"meal_type": "lunch", "nutrition": {"protein_g": protein}},
    )


def _log_then_ask(client, app_provider) -> dict:
    """One ingest turn and one query turn; returns the query turn's chat payload."""
    app_provider.plan_calls = [ToolCall(tool=LOG_MEMORY, arguments={"text": "lunch"})]
    app_provider.events = [_meal_event()]
    assert client.post("/api/chat", json={"message": "lunch: 200g chicken"}).status_code == 200

    app_provider.plan_calls = [
        ToolCall(tool=AGGREGATE_MEMORIES, arguments={"metric": "protein_g", **RANGE})
    ]
    app_provider.events = []
    response = client.post("/api/chat", json={"message": "how much protein?"})
    assert response.status_code == 200, response.text
    return response.json()


# ── auth ──────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("method,path", ROUTES)
def test_every_glassbox_route_requires_authentication(client, method, path) -> None:
    assert getattr(client, method)(path(uuid.uuid4())).status_code == 401


def test_batch_requires_authentication(client) -> None:
    response = client.post("/api/memories/batch", json={"ids": [str(uuid.uuid4())]})
    assert response.status_code == 401


# ── the trace endpoint ────────────────────────────────────────────────────────────────
def test_trace_is_served_verbatim_with_its_citation_verdict(client, app_provider) -> None:
    """I-29: what comes back is what was stored, not something recomputed from live data."""
    _signup(client)
    chat = _log_then_ask(client, app_provider)
    turn_id = chat["turn_id"]
    assert turn_id, "precondition: stage (G) recorded the turn"

    response = client.get(f"/api/turns/{turn_id}/trace")
    assert response.status_code == 200, response.text
    payload = response.json()

    assert payload["trace"] == chat["trace"], "the stored trace, byte for byte"
    assert payload["answer"] == chat["answer"]
    assert payload["citation_report"] == chat["citation_report"], (
        "recomputing from the persisted answer + citable_ids must reproduce the turn's verdict"
    )


def test_unknown_turn_is_404(client, app_provider) -> None:
    _signup(client)
    assert client.get(f"/api/turns/{uuid.uuid4()}/trace").status_code == 404


def test_another_users_trace_is_indistinguishable_from_a_missing_one(
    client, app_provider
) -> None:
    """I-28. 404, not 403 — a different status would confirm the turn exists."""
    _signup(client)
    chat = _log_then_ask(client, app_provider)
    victim_turn = chat["turn_id"]

    _logout(client)
    _signup(client)  # a different account

    assert client.get(f"/api/turns/{victim_turn}/trace").status_code == 404


# ── history ───────────────────────────────────────────────────────────────────────────
def test_turns_list_reports_which_turns_have_a_glass_box(client, app_provider) -> None:
    _signup(client)
    _log_then_ask(client, app_provider)

    payload = client.get("/api/turns").json()
    roles = [t["role"] for t in payload["turns"]]

    assert roles.count("user") == 2 and roles.count("assistant") == 2
    assistants = [t for t in payload["turns"] if t["role"] == "assistant"]
    assert all(t["has_trace"] for t in assistants), "every assistant turn assembled context"
    assert not any(t["has_trace"] for t in payload["turns"] if t["role"] == "user")


def test_turns_list_never_shows_another_users_conversation(client, app_provider) -> None:
    """I-28."""
    _signup(client)
    _log_then_ask(client, app_provider)

    _logout(client)
    _signup(client)

    assert client.get("/api/turns").json()["turns"] == []


def test_turns_list_filters_by_the_caller_s_raw_thread_id(client, app_provider) -> None:
    """The filter must accept the same raw id `POST /api/chat` was given — not the namespaced
    `user_id:thread_id` string `turns.thread_id` actually stores. A caller that only ever deals
    in its own thread ids (the "New chat" feature) must be able to filter its own history
    without knowing that internal scheme exists."""
    _signup(client)
    app_provider.plan_calls = [ToolCall(tool=LOG_MEMORY, arguments={"text": "lunch"})]
    app_provider.events = [_meal_event()]
    first = client.post(
        "/api/chat", json={"message": "lunch: 200g chicken", "thread_id": "thread-a"}
    ).json()
    second = client.post(
        "/api/chat", json={"message": "dinner: 3 eggs", "thread_id": "thread-b"}
    ).json()
    assert first["thread_id"] == "thread-a"
    assert second["thread_id"] == "thread-b"

    only_a = client.get("/api/turns", params={"thread_id": "thread-a"}).json()["turns"]
    assert [t["content"] for t in only_a if t["role"] == "user"] == ["lunch: 200g chicken"]

    only_b = client.get("/api/turns", params={"thread_id": "thread-b"}).json()["turns"]
    assert [t["content"] for t in only_b if t["role"] == "user"] == ["dinner: 3 eggs"]

    everything = client.get("/api/turns").json()["turns"]
    assert len(everything) == len(only_a) + len(only_b)


# ── threads (sidebar) ─────────────────────────────────────────────────────────────────
def test_threads_list_orders_by_recency_with_raw_ids_and_first_message_preview(
    client, app_provider
) -> None:
    """The list a caller gets back must be usable without knowing `turns.thread_id` is
    `user_id:thread_id` internally (same posture as `test_turns_list_filters_by_the_caller_s_
    raw_thread_id`), ordered by whichever thread was touched most recently, and labeled by the
    first thing the user actually typed — never a generated summary that could drift from it."""
    _signup(client)
    app_provider.plan_calls = [ToolCall(tool=LOG_MEMORY, arguments={"text": "lunch"})]
    app_provider.events = [_meal_event()]
    client.post("/api/chat", json={"message": "lunch: 200g chicken", "thread_id": "thread-a"})
    client.post("/api/chat", json={"message": "second message", "thread_id": "thread-a"})
    client.post("/api/chat", json={"message": "dinner: 3 eggs", "thread_id": "thread-b"})

    threads = client.get("/api/threads").json()["threads"]

    assert [t["thread_id"] for t in threads] == ["thread-b", "thread-a"], (
        "most recently active thread first"
    )
    by_id = {t["thread_id"]: t for t in threads}
    assert by_id["thread-a"]["preview"] == "lunch: 200g chicken", (
        "the FIRST user message, not the latest"
    )
    assert by_id["thread-b"]["preview"] == "dinner: 3 eggs"


def test_threads_list_never_shows_another_users_conversation(client, app_provider) -> None:
    """I-28."""
    _signup(client)
    _log_then_ask(client, app_provider)

    _logout(client)
    _signup(client)

    assert client.get("/api/threads").json()["threads"] == []


# ── batch hydration (T16) ─────────────────────────────────────────────────────────────
def test_batch_hydrates_cited_ids_in_one_request(client, app_provider) -> None:
    _signup(client)
    chat = _log_then_ask(client, app_provider)
    citable = chat["trace"]["citable_ids"]
    assert citable, "precondition: the aggregate contributed citable ids"

    response = client.post("/api/memories/batch", json={"ids": citable})
    assert response.status_code == 200, response.text
    payload = response.json()

    assert {m["id"] for m in payload["memories"]} == set(citable)
    assert payload["missing"] == []
    assert payload["memories"][0]["payload"], "hydration carries the payload a chip renders"


def test_batch_omits_rather_than_rejects_unreachable_ids(client, app_provider) -> None:
    """One stale chip must not fail the whole evidence pane — but the UI is told."""
    _signup(client)
    chat = _log_then_ask(client, app_provider)
    real = chat["trace"]["citable_ids"][0]
    ghost = str(uuid.uuid4())

    payload = client.post("/api/memories/batch", json={"ids": [real, ghost]}).json()

    assert [m["id"] for m in payload["memories"]] == [real]
    assert payload["missing"] == [ghost]


def test_batch_never_returns_another_users_memory(client, app_provider) -> None:
    """I-28 — and it lands in ``missing``, which is what a nonexistent id does too."""
    _signup(client)
    chat = _log_then_ask(client, app_provider)
    victim_id = chat["trace"]["citable_ids"][0]

    _logout(client)
    _signup(client)

    payload = client.post("/api/memories/batch", json={"ids": [victim_id]}).json()
    assert payload["memories"] == []
    assert payload["missing"] == [victim_id]


def test_batch_rejects_an_oversized_request(client, app_provider) -> None:
    _signup(client)
    ids = [str(uuid.uuid4()) for _ in range(201)]
    assert client.post("/api/memories/batch", json={"ids": ids}).status_code == 422


# ── stats + timeline, including the empty account (T11) ───────────────────────────────
def test_new_account_gets_honest_zeros_not_an_error(client) -> None:
    """Every account starts empty (ADR-13.4). A brand-new one must render, not break —
    this is the shape T11's "your memory starts here" empty states are built on."""
    _signup(client)

    stats = client.get("/api/stats").json()
    assert stats == {
        "memories": 0,
        "insights": 0,
        "days": 0,
        "first_event": None,
        "last_event": None,
    }
    assert client.get("/api/timeline").json() == {"days": []}


def test_stats_and_timeline_count_only_the_callers_memories(client, app_provider) -> None:
    """I-28, on both aggregate endpoints."""
    _signup(client)
    _log_then_ask(client, app_provider)
    assert client.get("/api/stats").json()["memories"] >= 1

    _logout(client)
    _signup(client)

    assert client.get("/api/stats").json()["memories"] == 0
    assert client.get("/api/timeline").json()["days"] == []
