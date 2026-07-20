"""The ``Memory`` write-model — one row of the ``memories`` table under construction.

Read paths (Phase 3 retrieval) return plain dict rows; this dataclass is the shape the
ingestion pipeline builds *before* inserting. Keeping it a dumb value object keeps the
engine's clean-package boundary (ADR-6).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


def vector_literal(vec: list[float]) -> str:
    """Format a vector for a ``%s::VECTOR(512)`` cast (same encoding as the T1 canary)."""
    return "[" + ",".join(f"{x:.9f}" for x in vec) + "]"


@dataclass
class Memory:
    user_id: UUID
    event_time: datetime
    tz: str
    type: str
    source: str          # 'chat' | 'photo_upload' | 'file_upload' | 'replay' | ...
    provenance: str      # 'live' | 'reconstructed'
    confidence: float    # 0..1
    payload: dict
    summary: str | None = None
    embedding: list[float] | None = None  # None -> NULL column, backfilled later (T15)
    status: str = "active"                 # 'active' | 'retracted' | 'superseded'
    superseded_by: UUID | None = None
    id: UUID | None = None                 # assigned by the DB on insert
    created_at: datetime | None = None
