"""POST /api/chat — the agent's front door (M6).

Full-stack: real FastAPI app, real CockroachDB, real graph and checkpointer, scripted
FakeModelProvider standing in for Bedrock. These assert the *transport* contract — auth,
thread scoping, response mapping, honest degradation — while routing itself is covered by
agent/tests/test_graph_routing.py.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from agent.tools import AGGREGATE_MEMORIES, COUNT_EVENTS, LOG_MEMORY
from api.routers.chat import MAX_CLIENT_THREAD_ID, thread_key
from api.tests.conftest import unique_email
from engine.model import ExtractedEvent, NarrationError, PlanningError, ToolCall

UTC = timezone.utc
NOW = datetime.now(UTC)
RANGE = {
    "start": (NOW - timedelta(days=30)).isoformat(),
    "end": (NOW + timedelta(days=1)).isoformat(),
}


def _signup(client) -> str:
    email = unique_email()
    response = client.post("/api/auth/signup", json={"email": email, "password": "pw-123456"})
    assert response.status_code in (200, 201), response.text
    return email


def _chat(client, message: str, thread_id: str | None = None):
    body: dict = {"message": message}
    if thread_id is not None:
        body["thread_id"] = thread_id
    return client.post("/api/chat", json=body)


def _meal_event(protein: float = 30.0) -> ExtractedEvent:
    return ExtractedEvent(
        type="meal",
        event_time=NOW - timedelta(hours=1),
        tz="Asia/Kolkata",
        confidence=0.9,
        summary="lunch: 200g chicken",
        payload={"meal_type": "lunch", "nutrition": {"protein_g": protein}},
    )


# ── auth + availability ───────────────────────────────────────────────────────────────
def test_chat_requires_authentication(client) -> None:
    assert _chat(client, "hello").status_code == 401


@pytest.mark.parametrize("body", [{"message": "  "}, {"message": "hi", "thread_id": ""}])
def test_malformed_bodies_are_rejected(client, app_provider, body) -> None:
    _signup(client)
    assert client.post("/api/chat", json=body).status_code == 422


def test_overlong_thread_id_is_rejected(client, app_provider) -> None:
    _signup(client)
    response = _chat(client, "hi", thread_id="x" * (MAX_CLIENT_THREAD_ID + 1))
    assert response.status_code == 422


# ── the response contract ─────────────────────────────────────────────────────────────
def test_query_turn_returns_answer_citations_and_trace(client, app_provider) -> None:
    _signup(client)
    app_provider.plan_calls = [ToolCall(tool=COUNT_EVENTS, arguments={"type": "meal", **RANGE})]

    response = _chat(client, "how many meals have I logged?")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert set(payload) == {"thread_id", "answer", "citations", "receipts", "trace", "errors"}
    assert payload["answer"]
    assert payload["trace"] is not None
    assert [s["family"] for s in payload["trace"]["retrieval_steps"]] == ["lookup"]
    assert payload["receipts"] == []
    assert payload["errors"] == []


def test_logged_memory_is_returned_as_a_receipt_and_is_readable(client, app_provider) -> None:
    _signup(client)
    app_provider.events = [_meal_event()]
    app_provider.plan_calls = [ToolCall(tool=LOG_MEMORY, arguments={"text": "200g chicken"})]

    payload = _chat(client, "just had 200g chicken").json()

    assert len(payload["receipts"]) == 1
    receipt = payload["receipts"][0]
    assert receipt["parse_status"] == "ok"
    memory_id = receipt["created"][0]["id"]
    # The same shape /api/ingest emits, and the memory is really there.
    stored = client.get(f"/api/memories/{memory_id}")
    assert stored.status_code == 200
    assert stored.json()["type"] == "meal"


def test_citations_resolve_to_readable_memories(client, app_provider) -> None:
    """The glass-box contract in miniature: every cited id is fetchable by its owner."""
    _signup(client)
    app_provider.events = [_meal_event()]
    app_provider.plan_calls = [ToolCall(tool=LOG_MEMORY, arguments={"text": "200g chicken"})]
    _chat(client, "had 200g chicken")

    app_provider.plan_calls = [
        ToolCall(tool=AGGREGATE_MEMORIES, arguments={"metric": "protein_g", **RANGE})
    ]
    payload = _chat(client, "how much protein today?").json()

    assert payload["citations"], "the aggregate should have citable evidence"
    for memory_id in payload["citations"]:
        assert client.get(f"/api/memories/{memory_id}").status_code == 200


def test_empty_account_answers_honestly(client, app_provider) -> None:
    _signup(client)
    app_provider.plan_calls = [
        ToolCall(tool=AGGREGATE_MEMORIES, arguments={"metric": "protein_g", **RANGE})
    ]
    payload = _chat(client, "how much protein this month?").json()

    assert payload["citations"] == []
    assert payload["trace"]["evidence"] == []
    assert payload["answer"]  # an answer, not an error


def test_conversational_turn_needs_no_memory_operation(client, app_provider) -> None:
    _signup(client)
    app_provider.plan_calls = []  # M4-2 empty plan
    app_provider.narration = "You're welcome!"

    payload = _chat(client, "thanks!").json()

    assert payload["answer"] == "You're welcome!"
    assert payload["citations"] == [] and payload["receipts"] == []
    assert app_provider.extract_calls == 0


def test_invalid_retrieval_call_is_surfaced_not_swallowed(client, app_provider) -> None:
    _signup(client)
    app_provider.plan_calls = [
        ToolCall(tool=AGGREGATE_MEMORIES, arguments={"metric": "cholesterol", **RANGE}),
        ToolCall(tool=COUNT_EVENTS, arguments={"type": "meal", **RANGE}),
    ]
    payload = _chat(client, "cholesterol and meals?").json()

    assert len(payload["errors"]) == 1 and "cholesterol" in payload["errors"][0]
    assert len(payload["trace"]["retrieval_steps"]) == 1  # the valid call still ran
    assert payload["answer"]


@pytest.mark.parametrize("failure", ["plan", "narrate"])
def test_model_failure_maps_to_502(client, app_provider, failure) -> None:
    _signup(client)
    if failure == "plan":
        app_provider.plan_error = True
    else:
        app_provider.narrate_error = True

    response = _chat(client, "how much protein?")
    assert response.status_code == 502
    assert "unavailable" in response.json()["detail"]


def test_provider_errors_are_the_typed_ones(app_provider) -> None:
    # Guards the mapping above against drifting to a bare Exception.
    app_provider.plan_error = True
    with pytest.raises(PlanningError):
        app_provider.plan("q", [], now=NOW, tz="UTC")
    app_provider.plan_error = False
    app_provider.narrate_error = True
    with pytest.raises(NarrationError):
        app_provider.narrate("q", None)


# ── threads: continuity and user scoping ──────────────────────────────────────────────
def test_thread_id_is_returned_and_generated_when_absent(client, app_provider) -> None:
    _signup(client)
    app_provider.plan_calls = []

    generated = _chat(client, "hi").json()["thread_id"]
    assert generated
    echoed = _chat(client, "hi again", thread_id=generated).json()["thread_id"]
    assert echoed == generated


def test_conversation_continues_on_the_same_thread(client, app_provider) -> None:
    _signup(client)
    app_provider.plan_calls = []
    thread = f"t-{uuid.uuid4().hex[:8]}"

    _chat(client, "first message", thread_id=thread)
    _chat(client, "second message", thread_id=thread)

    saver_graph = client.app.state.graph
    state = saver_graph.get_state(
        {"configurable": {"thread_id": thread_key(_user_id(client), thread)}}
    )
    contents = [m.content for m in state.values["messages"]]
    assert "first message" in contents and "second message" in contents


def test_threads_are_user_scoped(client, app_provider) -> None:
    """User B presenting user A's thread_id must not read A's conversation — it silently
    becomes B's own thread (ADR-13.4 posture: existence is not probeable)."""
    _signup(client)
    app_provider.plan_calls = []
    shared_id = f"shared-{uuid.uuid4().hex[:8]}"
    _chat(client, "user A secret message", thread_id=shared_id)
    a_key = thread_key(_user_id(client), shared_id)

    client.post("/api/auth/logout")
    _signup(client)  # now user B, same TestClient/cookie jar
    payload = _chat(client, "user B message", thread_id=shared_id).json()
    b_key = thread_key(_user_id(client), shared_id)

    assert a_key != b_key
    graph = client.app.state.graph
    b_messages = [
        m.content
        for m in graph.get_state({"configurable": {"thread_id": b_key}}).values["messages"]
    ]
    assert "user A secret message" not in b_messages
    assert "user B message" in b_messages
    assert payload["answer"]


def _user_id(client):
    """The signed-in user's id, resolved the same way the app does (session cookie)."""
    from api.auth import resolve_session
    from api.deps import SESSION_COOKIE

    token = client.cookies.get(SESSION_COOKIE)
    with client.app.state.db.transaction() as cur:
        return resolve_session(cur, token)
