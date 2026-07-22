"""Parameterized, user-scoped write queries for the ``memories`` table.

**Security invariant (ADR-13.4, the scoping boundary):** every query in this module filters
on ``user_id``. There is no code path that reads or mutates a memory without scoping it to
its owner. The security test (api/tests/test_scoping.py) exists to keep this honest.

All queries are parameterized — the engine has no raw-SQL path exposed to the agent
(03-memory-engine.md). Inserts are row-at-a-time by design: C-SPANN vector-index inserts
degrade in large batches (the T1 canary and T8 replay guard the same footgun).
"""

from __future__ import annotations

from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from engine.memory import Memory, vector_literal

# Columns selected for read-side reconstruction (embedding is never SELECTed as a vector;
# callers ask for its presence via a boolean instead).
_READ_COLS = (
    "id, user_id, event_time, tz, type, source, provenance, confidence, "
    "status, superseded_by, summary, payload, created_at, "
    "(embedding IS NOT NULL) AS has_embedding"
)


def insert_memory(cur: psycopg.Cursor, m: Memory) -> UUID:
    """Insert one memory, returning its new id. Call inside a transaction."""
    if m.embedding is None:
        emb_sql, emb_params = "NULL", []
    else:
        emb_sql, emb_params = "%s::VECTOR(512)", [vector_literal(m.embedding)]

    cur.execute(
        f"""
        INSERT INTO memories
            (user_id, event_time, tz, type, source, provenance, confidence,
             status, superseded_by, summary, payload, embedding)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, {emb_sql})
        RETURNING id
        """,
        [
            m.user_id, m.event_time, m.tz, m.type, m.source, m.provenance, m.confidence,
            m.status, m.superseded_by, m.summary, Jsonb(m.payload), *emb_params,
        ],
    )
    return cur.fetchone()["id"]


def insert_memories(cur: psycopg.Cursor, memories: list[Memory]) -> list[UUID]:
    """Insert several memories in one transaction (atomic turn — all or none)."""
    return [insert_memory(cur, m) for m in memories]


def mark_superseded(
    cur: psycopg.Cursor, user_id: UUID, memory_id: UUID, superseded_by: UUID
) -> None:
    """Flip a memory to ``status='superseded'`` and chain it to its replacement (never
    deletes — ADR-9). Used by reprocess_note. Scoped and only touches active rows."""
    cur.execute(
        """
        UPDATE memories
        SET status = 'superseded', superseded_by = %s
        WHERE id = %s AND user_id = %s AND status = 'active'
        """,
        [superseded_by, memory_id, user_id],
    )


def fetch_unembedded(cur: psycopg.Cursor, user_id: UUID, limit: int) -> list[dict]:
    """Return active, embeddable rows still missing an embedding (T15 backfill source).
    Only id + summary are needed to re-embed."""
    cur.execute(
        """
        SELECT id, summary
        FROM memories
        WHERE user_id = %s AND embedding IS NULL AND status = 'active'
              AND summary IS NOT NULL
        ORDER BY created_at
        LIMIT %s
        """,
        [user_id, limit],
    )
    return cur.fetchall()


def set_embedding(
    cur: psycopg.Cursor, user_id: UUID, memory_id: UUID, embedding: list[float]
) -> None:
    """Attach a computed embedding to a previously-NULL row (backfill). Scoped."""
    cur.execute(
        "UPDATE memories SET embedding = %s::VECTOR(512) WHERE id = %s AND user_id = %s",
        [vector_literal(embedding), memory_id, user_id],
    )


def get_memory(cur: psycopg.Cursor, user_id: UUID, memory_id: UUID) -> dict | None:
    """Fetch a single memory scoped to its owner. Returns None if it doesn't exist OR
    belongs to another user — the two are indistinguishable to a caller by design, which
    is what makes cross-user probing a dead end (scoping security test)."""
    cur.execute(
        f"SELECT {_READ_COLS} FROM memories WHERE id = %s AND user_id = %s",
        [memory_id, user_id],
    )
    return cur.fetchone()


def get_note(cur: psycopg.Cursor, user_id: UUID, note_id: UUID) -> dict | None:
    """Return an active note's raw text **and its origin** (``source``, ``provenance``) for
    reprocessing, or None if not an eligible active note owned by this user.

    The origin columns are what let ``reprocess_note`` re-emit typed events carrying the same
    provenance the note was written with — a reconstructed note must never be upgraded into
    memories labelled ``live`` (ADR-13.10 honesty posture; replay pushes ``reconstructed``
    through this same pipeline, see 03-memory-engine.md §2)."""
    cur.execute(
        """
        SELECT payload ->> 'text' AS text, source, provenance
        FROM memories
        WHERE id = %s AND user_id = %s AND type = 'note' AND status = 'active'
        """,
        [note_id, user_id],
    )
    return cur.fetchone()
