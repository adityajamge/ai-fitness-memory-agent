"""Glass-box read routes (M3 / T7 · T16).

Transport only, like every other router here: authenticate, call the engine, shape JSON. No
intelligence, no SQL, no re-derivation.

**Every route is user-scoped, and that is asserted per route (I-28)** in
``api/tests/test_glassbox.py`` rather than left to a ``WHERE`` clause a future refactor could
drop. A resource belonging to someone else returns 404, identical to one that does not exist —
cross-user probing gets no signal.

The data-source rule (ADR-12) is what these routes exist to serve: everything structured in
the UI comes from the deterministic trace and these endpoints. Model output is never parsed to
build glass-box data.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from api.deps import get_current_user
from api.routers.chat import thread_key
from engine.citations import validate_citations
from engine.db import Database
from engine.glassbox import (
    MAX_BATCH,
    MAX_TURNS_PAGE,
    fetch_memories,
    fetch_stats,
    fetch_timeline,
    fetch_trace,
    fetch_turns,
)

router = APIRouter(prefix="/api", tags=["glass-box"])


class BatchBody(BaseModel):
    ids: list[UUID] = Field(min_length=1, max_length=MAX_BATCH)


def _db(request: Request) -> Database:
    return request.app.state.db


def _memory_json(row: dict) -> dict:
    return {
        "id": str(row["id"]),
        "event_time": row["event_time"].isoformat(),
        "tz": row["tz"],
        "type": row["type"],
        "source": row["source"],
        "provenance": row["provenance"],
        "confidence": row["confidence"],
        "status": row["status"],
        "superseded_by": str(row["superseded_by"]) if row["superseded_by"] else None,
        "summary": row["summary"],
        "payload": row["payload"],
        "created_at": row["created_at"].isoformat(),
    }


@router.get("/turns/{turn_id}/trace")
def get_trace(
    turn_id: UUID, request: Request, user_id: UUID = Depends(get_current_user)
) -> dict:
    """The glass box for one turn: the stored trace, verbatim, plus its citation verdict.

    The trace is returned **exactly as persisted** (I-29). The citation report is recomputed
    here from the stored answer and the trace's own ``citable_ids`` — deterministic, and
    bit-identical to the verdict the turn produced, because it is a pure function of two values
    that were both persisted (§4.4). That is why it is derived rather than stored: one source
    of truth, no drift.
    """
    with _db(request).transaction() as cur:
        row = fetch_trace(cur, user_id, turn_id)
    if row is None:
        raise HTTPException(status_code=404, detail="trace not found")

    trace = row["trace"]
    citable = {UUID(i) for i in trace.get("citable_ids", [])}
    report = validate_citations(row["answer"] or "", citable)
    return {
        "turn_id": str(turn_id),
        "thread_id": row["thread_id"],
        "answer": row["answer"],
        "memory_ids": [str(m) for m in (row["memory_ids"] or [])],
        "trace": trace,
        "citation_report": report.to_json(),
        "recorded_at": row["created_at"].isoformat(),
    }


@router.get("/turns")
def list_turns(
    request: Request,
    thread_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=MAX_TURNS_PAGE),
    user_id: UUID = Depends(get_current_user),
) -> dict:
    """Conversation history, oldest last. ``has_trace`` tells the UI which turns have a glass
    box to open without hydrating any of them.

    ``thread_id`` is the **raw client-supplied id** — the same string a caller would pass to
    ``POST /api/chat`` — never the namespaced one stored in ``turns.thread_id``. Namespacing it
    here, the same way ``chat.py``'s write path does before it ever reaches the checkpointer or
    stage (G), is what keeps that scheme an internal detail: a client that only ever deals in
    its own raw thread ids can filter its own history without knowing this table's keys are
    ``user_id:thread_id`` under the hood.
    """
    namespaced = thread_key(user_id, thread_id) if thread_id else None
    with _db(request).transaction() as cur:
        rows = fetch_turns(cur, user_id, thread_id=namespaced, limit=limit)
    return {
        "turns": [
            {
                "id": str(r["id"]),
                "role": r["role"],
                "content": r["content"],
                "thread_id": r["thread_id"],
                "memory_ids": [str(m) for m in (r["memory_ids"] or [])],
                "has_trace": r["has_trace"],
                "created_at": r["created_at"].isoformat(),
            }
            for r in rows
        ]
    }


@router.post("/memories/batch")
def batch_memories(
    body: BatchBody, request: Request, user_id: UUID = Depends(get_current_user)
) -> dict:
    """Hydrate many memory IDs in one round trip (T16).

    POST rather than GET because a turn's citable set can exceed what belongs in a query
    string, and because these IDs are user data that has no business in access logs.

    IDs the caller cannot see are **omitted, not rejected**: a trace may cite a row that was
    later superseded, and one stale chip must not fail the whole evidence pane. ``missing``
    names them so the UI can render an honest placeholder rather than silently showing fewer
    rows than the answer cited.
    """
    with _db(request).transaction() as cur:
        rows = fetch_memories(cur, user_id, list(body.ids))
    found = {row["id"] for row in rows}
    return {
        "memories": [_memory_json(r) for r in rows],
        "missing": [str(i) for i in body.ids if i not in found],
    }


@router.get("/stats")
def get_stats(request: Request, user_id: UUID = Depends(get_current_user)) -> dict:
    """Top-bar stats. All zeros is a valid, honest response for a new account (T11)."""
    with _db(request).transaction() as cur:
        row = fetch_stats(cur, user_id)
    return {
        "memories": row["memories"],
        "insights": row["insights"],
        "days": row["days"],
        "first_event": row["first_event"].isoformat() if row["first_event"] else None,
        "last_event": row["last_event"].isoformat() if row["last_event"] else None,
    }


@router.get("/timeline")
def get_timeline(request: Request, user_id: UUID = Depends(get_current_user)) -> dict:
    """Memory density per day for the timeline strip. An empty list is a new account."""
    with _db(request).transaction() as cur:
        rows = fetch_timeline(cur, user_id)
    return {
        "days": [
            {"day": r["day"].isoformat(), "n": r["n"], "insights": r["insights"]}
            for r in rows
        ]
    }
