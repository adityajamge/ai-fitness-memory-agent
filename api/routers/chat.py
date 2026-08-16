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

import io
import json
import logging
from uuid import UUID, uuid4

import psycopg
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, field_validator

from agent.graph import STAGE_LABELS, TurnResult, run_turn, run_turn_stream
from api.deps import get_current_user
from api.routers.ingest import receipt_json
from engine.assembly import assemble
from engine.db import Database
from engine.ingestion import IngestionService, Receipt
from engine.model import NarrationError, PlanningError
from engine.turns import persist_turn

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["chat"])

# The checkpointer requires thread_id < 255 chars (ADR-13.14 footgun). We prepend a 36-char
# UUID plus a separator, so the client's half is capped well inside that budget.
MAX_CLIENT_THREAD_ID = 128

# M7 (photo ingestion, ephemeral-storage variant — TODOS.md). Generous for a phone photo,
# small enough to keep a vision call's latency reasonable; enforced before the bytes ever
# reach a model call.
_PHOTO_MAX_BYTES = 8 * 1024 * 1024
_PHOTO_CONTENT_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})


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
            # exc_info surfaces the full chained traceback (PlanningError -> anthropic's
            # APIError -> the underlying httpx transport error), which the plain str() here
            # was hiding -- that chain is what actually says DNS failure vs. TCP timeout vs.
            # TLS error, and "Connection error." alone doesn't distinguish those.
            logger.warning("chat stream failed for user %s: %s", user_id, exc, exc_info=True)
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


def _validate_photo(content_type: str | None, data: bytes) -> None:
    """Reject anything that isn't a small, genuinely-decodable image before it reaches a
    model call. The content-type allowlist matches the providers' own vision allowlists
    (agent/providers/bedrock.py, claude_api.py) — this is the first line of defense, not the
    only one. Decoding with Pillow (rather than trusting the declared content-type) is what
    catches a mislabeled or malformed file; ``verify()`` also means the bytes are never
    written anywhere, satisfying M7's no-persistence tradeoff by construction."""
    if content_type not in _PHOTO_CONTENT_TYPES:
        raise HTTPException(
            status_code=422, detail="unsupported image type; use JPEG, PNG, or WebP"
        )
    if not data:
        raise HTTPException(status_code=422, detail="empty upload")
    if len(data) > _PHOTO_MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"image too large; max {_PHOTO_MAX_BYTES // (1024 * 1024)}MB",
        )
    try:
        with Image.open(io.BytesIO(data)) as img:
            img.verify()
    except (UnidentifiedImageError, OSError) as exc:
        raise HTTPException(status_code=422, detail="file is not a readable image") from exc


def _photo_answer(receipt: Receipt) -> str:
    """Deterministic answer text built from the receipt alone — no extra model call, and
    nothing said here the receipt doesn't already contain. ``created`` summaries are the
    vision model's own short rendering of what it saw (engine/model.py ExtractedEvent.summary),
    so this never invents anything beyond what was already extracted."""
    names = [ref.summary for ref in receipt.created if ref.summary]
    if names:
        return "Logged from your photo: " + "; ".join(names)
    return receipt.message


@router.post("/chat/photo")
def chat_photo(
    request: Request,
    image: UploadFile = File(...),
    message: str = Form(""),
    thread_id: str | None = Form(None),
    user_id: UUID = Depends(get_current_user),
) -> dict:
    """Photo counterpart to ``POST /api/chat`` (M7): attach a food photo, optionally with a
    caption, and get back the same turn shape — ``answer``/``citations``/``receipts``/
    ``trace``/``turn_id``/``errors`` — a text turn gets.

    Deliberately bypasses the LangGraph plan/retrieve/narrate spine: a photo attachment is an
    unambiguous ingest signal needing no NL classification, the same posture already taken for
    ``log_memory`` (dispatched directly in ``agent/graph.py``'s ``ingest_node`` rather than run
    through retrieval). It still calls ``assemble``/``persist_turn`` directly — exactly what a
    pure-ingest graph turn already does after ``ingest_node`` — so the turn shows up correctly
    in ``GET /api/turns`` history and ``GET /api/turns/{id}/trace`` like any other turn.
    """
    data = image.file.read()
    _validate_photo(image.content_type, data)
    if thread_id is not None and (not thread_id.strip() or len(thread_id) > MAX_CLIENT_THREAD_ID):
        raise HTTPException(status_code=422, detail="invalid thread_id")

    settings = request.app.state.settings
    svc: IngestionService = request.app.state.ingestion
    db: Database = request.app.state.db

    client_thread_id = thread_id or uuid4().hex
    caption = message.strip()
    question = caption or "(photo)"

    try:
        receipt = svc.ingest_photo(
            user_id, data, image.content_type, caption=caption, tz=settings.default_tz
        )
        answer = _photo_answer(receipt)
        _, trace = assemble(question, [])
        with db.transaction() as cur:
            turn_record = persist_turn(
                cur,
                user_id=user_id,
                thread_id=thread_key(user_id, client_thread_id),
                question=question,
                answer=answer,
                memory_ids=[ref.id for ref in receipt.created],
                trace=trace,
            )
    except psycopg.OperationalError as exc:
        logger.warning("photo turn failed for user %s: database connection lost: %s", user_id, exc)
        raise HTTPException(
            status_code=503, detail="chat is temporarily unavailable"
        ) from exc

    return {
        "thread_id": client_thread_id,
        "answer": answer,
        "citations": [],
        "citation_report": None,
        "receipts": [receipt_json(receipt)],
        "trace": trace.to_json(),
        "turn_id": str(turn_record.assistant_turn_id),
        "errors": [],
    }
