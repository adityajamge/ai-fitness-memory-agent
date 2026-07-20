"""Ingestion + memory-read routes (Phase 2 write path behind auth).

POST /api/ingest is the acceptance-curl target: a signed-in session logs text and gets a
receipt. GET /api/memories/{id} exists for verification and drives the scoping security test
(a memory is only ever visible to its owner)."""

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


def _receipt_json(receipt: Receipt) -> dict:
    return {
        "parse_status": receipt.parse_status,
        "message": receipt.message,
        "superseded_note_id": str(receipt.superseded_note_id)
        if receipt.superseded_note_id
        else None,
        "created": [
            {
                "id": str(ref.id),
                "type": ref.type,
                "summary": ref.summary,
                "embedding_pending": ref.embedding_pending,
            }
            for ref in receipt.created
        ],
    }


@router.post("/ingest")
def ingest(body: IngestBody, request: Request, user_id: UUID = Depends(get_current_user)) -> dict:
    svc: IngestionService = request.app.state.ingestion
    receipt = svc.ingest_text(user_id, body.text)
    return _receipt_json(receipt)


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
