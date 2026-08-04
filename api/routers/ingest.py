"""Ingestion + memory-read routes (Phase 2 write path behind auth).

POST /api/ingest is the acceptance-curl target: a signed-in session logs text and gets a
receipt. POST /api/memories/{id}/reprocess retries a note that previously failed to parse
(supersede-on-retry, D2). GET /api/memories/{id} exists for verification and drives the
scoping security test (a memory is only ever visible to its owner)."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, field_validator

from api.deps import get_current_user
from engine.db import Database
from engine.ingestion import IngestionService, Receipt
from engine.repository import get_memory

router = APIRouter(prefix="/api", tags=["memory"])


class IngestBody(BaseModel):
    text: str

    @field_validator("text")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("text must not be empty")
        return v


def _ref_json(ref) -> dict:
    return {
        "id": str(ref.id),
        "type": ref.type,
        "summary": ref.summary,
        "embedding_pending": ref.embedding_pending,
    }


def receipt_json(receipt: Receipt) -> dict:
    """The wire shape of an ingestion receipt. Public because ``/api/chat`` returns the same
    shape for the memories a turn created — one receipt contract, two entry points.

    ``insights`` is a **separate key**, not folded into ``created`` (Phase 5 §4.8): the user
    reported what is in ``created``, while an insight is a claim the engine made about it, and
    a UI that showed them as one list would imply the user logged something they never said.
    Additive — a client that ignores the key sees exactly the Phase 2 shape."""
    return {
        "parse_status": receipt.parse_status,
        "message": receipt.message,
        "superseded_note_id": str(receipt.superseded_note_id)
        if receipt.superseded_note_id
        else None,
        "created": [_ref_json(ref) for ref in receipt.created],
        "insights": [_ref_json(ref) for ref in receipt.insights],
    }


@router.post("/ingest")
def ingest(body: IngestBody, request: Request, user_id: UUID = Depends(get_current_user)) -> dict:
    svc: IngestionService = request.app.state.ingestion
    receipt = svc.ingest_text(user_id, body.text)
    return receipt_json(receipt)


@router.post("/memories/{memory_id}/reprocess")
def reprocess(memory_id: UUID, request: Request, user_id: UUID = Depends(get_current_user)) -> dict:
    """Retry extraction on a note that previously fell back (supersede-on-retry).

    Same receipt shape as /api/ingest: on success the note flips to `superseded` and the
    receipt carries `superseded_note_id`; if extraction still fails, 200 with
    `parse_status="incomplete"` and the note stays active — nothing lost, nothing duplicated.
    404 covers every ineligible target — nonexistent, another user's, not a note, or already
    superseded — indistinguishable by design, same posture as GET (no cross-user probing).
    """
    svc: IngestionService = request.app.state.ingestion
    try:
        receipt = svc.reprocess_note(user_id, memory_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="no reprocessable note found") from exc
    return receipt_json(receipt)


@router.get("/memories/{memory_id}")
def read_memory(
    memory_id: UUID, request: Request, user_id: UUID = Depends(get_current_user)
) -> dict:
    db: Database = request.app.state.db
    with db.transaction() as cur:
        row = get_memory(cur, user_id, memory_id)
    if row is None:
        raise HTTPException(status_code=404, detail="memory not found")
    return {
        "id": str(row["id"]),
        "type": row["type"],
        "event_time": row["event_time"].isoformat(),
        "tz": row["tz"],
        "source": row["source"],
        "provenance": row["provenance"],
        "confidence": row["confidence"],
        "status": row["status"],
        "summary": row["summary"],
        "payload": row["payload"],
        "has_embedding": row["has_embedding"],
    }
