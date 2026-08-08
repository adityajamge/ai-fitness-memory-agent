"""POST /api/chat/stream — the SSE twin of /api/chat (M6).

Full-stack, same posture as test_chat.py: real FastAPI app, real CockroachDB, real graph and
checkpointer, scripted FakeModelProvider. These assert that streaming adds progress narration
without changing the turn's outcome — the client's fallback to the plain endpoint depends on
that equivalence holding.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from agent.tools import COUNT_EVENTS, LOG_MEMORY
from api.tests.conftest import unique_email
from engine.model import ExtractedEvent, ToolCall

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


def _parse_sse(body: str) -> list[tuple[str, dict]]:
    """Minimal SSE frame parser — the same shape the frontend reader implements."""
    import json

    events = []
    for frame in body.strip().split("\n\n"):
        if not frame.strip():
            continue
        lines = frame.splitlines()
        event = next(line.removeprefix("event: ") for line in lines if line.startswith("event: "))
        data = next(line.removeprefix("data: ") for line in lines if line.startswith("data: "))
        events.append((event, json.loads(data)))
    return events


def test_stream_requires_authentication(client) -> None:
    response = client.post("/api/chat/stream", json={"message": "hello"})
    assert response.status_code == 401


def test_stream_emits_stage_events_then_done(client, app_provider) -> None:
    _signup(client)
    app_provider.plan_calls = [ToolCall(tool=COUNT_EVENTS, arguments={"type": "meal", **RANGE})]

    response = client.post("/api/chat/stream", json={"message": "how many meals?"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = _parse_sse(response.text)

    stages = [data["stage"] for kind, data in events if kind == "stage"]
    # A query turn runs retrieve -> assemble -> narrate; plan and persist are deliberately
    # unnarrated (STAGE_LABELS in agent/graph.py).
    assert stages == ["retrieve", "assemble", "narrate"]
    labels = [data["label"] for kind, data in events if kind == "stage"]
    assert all(isinstance(label, str) and label for label in labels)

    assert events[-1][0] == "done"
    payload = events[-1][1]
    assert payload["answer"]
    assert payload["trace"] is not None
    assert payload["turn_id"] is not None


def test_stream_done_payload_matches_the_plain_endpoint_shape(client, app_provider) -> None:
    """The two transports must be interchangeable from the caller's point of view — that
    equivalence is what makes falling back from SSE to the plain endpoint safe (DESIGN.md
    §11's open risk)."""
    _signup(client)
    app_provider.plan_calls = [ToolCall(tool=COUNT_EVENTS, arguments={"type": "meal", **RANGE})]

    plain = client.post("/api/chat", json={"message": "how many meals?"}).json()
    streamed = _parse_sse(
        client.post("/api/chat/stream", json={"message": "how many meals?"}).text
    )[-1][1]

    assert set(streamed) == set(plain)


def test_stream_ingest_turn_runs_extracting_then_generating(client, app_provider) -> None:
    _signup(client)
    app_provider.events = [
        ExtractedEvent(
            type="meal",
            event_time=NOW - timedelta(hours=1),
            tz="Asia/Kolkata",
            confidence=0.9,
            summary="lunch: 200g chicken",
            payload={"meal_type": "lunch", "nutrition": {"protein_g": 30.0}},
        )
    ]
    app_provider.plan_calls = [ToolCall(tool=LOG_MEMORY, arguments={"text": "200g chicken"})]

    events = _parse_sse(
        client.post("/api/chat/stream", json={"message": "just had 200g chicken"}).text
    )

    stages = [data["stage"] for kind, data in events if kind == "stage"]
    # `assemble` runs on every turn, even an ingest-only one with no context to retrieve
    # (assemble_node's docstring: "so a turn that narrates always has a context and a trace").
    assert stages == ["ingest", "assemble", "narrate"]

    payload = events[-1][1]
    assert len(payload["receipts"]) == 1
