"""The chat endpoint — the agent's front door (Phase 3 "bare chat").

This module is **transport only**: authenticate, map the request onto one graph turn, map
the turn's result back onto JSON. All intelligence lives below it — the graph orchestrates
(``agent/graph.py``), the engine retrieves and assembles, the provider narrates. Nothing
here interprets language or touches the database directly.

**Thread scoping (the security property).** The client's ``thread_id`` is opaque and
namespaced with the caller's ``user_id`` before it ever reaches the checkpointer, so two
users presenting the same string get two different threads. Replaying another user's
thread_id is therefore not a way to read their conversation — it silently starts a fresh
thread of your own, the same "indistinguishable by design" posture as
``GET /api/memories/{id}`` (ADR-13.4).

**Response shape** is designed so Phase 6 *adds* fields rather than reshaping: the trace
rides inline today (decision D-2) and becomes a ``trace_id`` fetch once T7 persists it.
"""

from __future__ import annotations

import json
import logging
from uuid import UUID, uuid4

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, field_validator

from agent.graph import STAGE_LABELS, TurnResult, run_turn, run_turn_stream
from api.deps import get_current_user
from api.routers.ingest import receipt_json
from engine.model import NarrationError, PlanningError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["chat"])

# The checkpointer requires thread_id < 255 chars (ADR-13.14 footgun). We prepend a 36-char
# UUID plus a separator, so the client's half is capped well inside that budget.
MAX_CLIENT_THREAD_ID = 128


class ChatBody(BaseModel):
    message: str
    thread_id: str | None = None

    @field_validator("message")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("message must not be empty")
        return v

    @field_validator("thread_id")
    @classmethod
    def _sane_thread(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if not v.strip():
            raise ValueError("thread_id must not be empty")
        if len(v) > MAX_CLIENT_THREAD_ID:
            raise ValueError(f"thread_id must be at most {MAX_CLIENT_THREAD_ID} characters")
        return v


def thread_key(user_id: UUID, client_thread_id: str) -> str:
    """Namespace a client thread id to its owner. This is what makes threads user-scoped:
    the checkpointer never sees a bare client-supplied key."""
    return f"{user_id}:{client_thread_id}"


def _turn_json(client_thread_id: str, result: TurnResult) -> dict:
    return {
        "thread_id": client_thread_id,
        "answer": result.answer,
        "citations": result.citations,
        # T7b's verdict (Phase 6 M2). `citations` above stays the plain list of resolved ids
        # it has always been, so nothing that already consumes it breaks; this adds the
        # *invalid* markers the UI flags in place, and the honest-scope distinction that goes
        # with them — "resolvable", never "verified" (ADR-13.13).
        "citation_report": (
            result.citation_report.to_json() if result.citation_report else None
        ),
        "receipts": [receipt_json(r) for r in result.receipts],
        # Inline for Phase 3; Phase 6 (T7) persists it and the UI fetches by trace_id.
        "trace": result.trace.to_json() if result.trace else None,
        # Stage (G)'s handle: what the glass-box read API (M3) fetches this turn by. Null
        # means the turn was not recorded — the answer stands, the glass box does not.
        "turn_id": (
            str(result.turn_record.assistant_turn_id) if result.turn_record else None
        ),
        # Retrieval calls the engine refused (e.g. an unknown metric). Surfaced rather than
        # swallowed: the answer may be partial and the caller deserves to know.
        "errors": result.errors,
    }


@router.post("/chat")
def chat(body: ChatBody, request: Request, user_id: UUID = Depends(get_current_user)) -> dict:
    """Run one conversational turn: plan → (ingest) → (retrieve) → assemble → narrate."""
    graph = getattr(request.app.state, "graph", None)
    if graph is None:
        # The graph is built at startup; absence means the database was unreachable then
        # (deploy-early tolerates that so the health check stays green).
        raise HTTPException(status_code=503, detail="chat is temporarily unavailable")

    settings = request.app.state.settings
    client_thread_id = body.thread_id or uuid4().hex

    try:
        result = run_turn(
            graph,
            user_id=user_id,
            question=body.message,
            thread_id=thread_key(user_id, client_thread_id),
            tz=settings.default_tz,
        )
    except (PlanningError, NarrationError) as exc:
        # The model failed on this turn. Nothing was half-written: ingestion commits
        # atomically and the checkpoint is only written at the end of a turn.
        logger.warning("chat turn failed for user %s: %s", user_id, exc)
        raise HTTPException(
            status_code=502, detail="the assistant is unavailable right now; please retry"
        ) from exc
    except psycopg.OperationalError as exc:
        # The checkpointer holds one long-lived connection for the app's lifetime
        # (api/main.py) — if the cluster ever drops it (idle timeout, restart), every turn
        # would otherwise crash with an unhandled 500. Same 503 posture as an absent graph:
        # a dropped connection isn't recoverable mid-request, only by restarting the app.
        logger.warning("chat turn failed for user %s: database connection lost: %s", user_id, exc)
        raise HTTPException(
            status_code=503, detail="chat is temporarily unavailable"
        ) from exc

    return _turn_json(client_thread_id, result)


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@router.post("/chat/stream")
def chat_stream(
    body: ChatBody, request: Request, user_id: UUID = Depends(get_current_user)
) -> StreamingResponse:
    """SSE twin of ``POST /api/chat`` — the live engine pane's transport (M6).

    Same turn, same validation, same graph; the only difference is that stage progress is
    narrated as it happens instead of arriving all at once in one response. The client falls
    back to the plain endpoint above when this connection never reaches a ``done``/``error``
    frame — DESIGN.md §11's "open risk": SSE through Express Mode's shared ALB is unproven —
    so this handler owes the same two outcomes ``chat()`` does, just framed as SSE events
    instead of HTTP status codes (headers are already sent by the time a graph error can
    surface, so a status code is no longer available to report it with).
    """
    graph = getattr(request.app.state, "graph", None)
    if graph is None:
        raise HTTPException(status_code=503, detail="chat is temporarily unavailable")

    settings = request.app.state.settings
    client_thread_id = body.thread_id or uuid4().hex

    def events():
        try:
            for item in run_turn_stream(
                graph,
                user_id=user_id,
                question=body.message,
                thread_id=thread_key(user_id, client_thread_id),
                tz=settings.default_tz,
            ):
                if isinstance(item, TurnResult):
                    yield _sse("done", _turn_json(client_thread_id, item))
                else:
                    yield _sse("stage", {"stage": item, "label": STAGE_LABELS[item]})
        except (PlanningError, NarrationError) as exc:
            logger.warning("chat stream failed for user %s: %s", user_id, exc)
            yield _sse(
                "error", {"detail": "the assistant is unavailable right now; please retry"}
            )
        except psycopg.OperationalError as exc:
            logger.warning(
                "chat stream failed for user %s: database connection lost: %s", user_id, exc
            )
            yield _sse("error", {"detail": "chat is temporarily unavailable"})

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # The standard opt-out for proxies that buffer text responses by default. Not proof
            # the ALB itself won't buffer — that risk is exactly what the client-side fallback
            # to the plain endpoint exists to cover.
            "X-Accel-Buffering": "no",
        },
    )
