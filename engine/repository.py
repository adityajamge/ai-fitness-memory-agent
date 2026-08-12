"""Parameterized, user-scoped write queries for the ``memories`` table.

**Security invariant (ADR-13.4, the scoping boundary):** every query in this module filters
on ``user_id``. There is no code path that reads or mutates a memory without scoping it to
its owner. The security test (api/tests/test_scoping.py) exists to keep this honest.

All queries are parameterized — the engine has no raw-SQL path exposed to the agent
(03-memory-engine.md). Inserts are row-at-a-time by design: C-SPANN vector-index inserts
degrade in large batches (the T1 canary and T8 replay guard the same footgun).
"""

from __future__ import annotations

from datetime import datetime
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


def fetch_meals_without_nutrition(
    cur: psycopg.Cursor, user_id: UUID, limit: int
) -> list[dict]:
    """Active meals that carry items but no nutrition object (nutrition-backfill source).

    ``payload ? 'nutrition'`` excludes anything already estimated **or** authored elsewhere, so
    a user-stated or reviewed value is never a backfill candidate in the first place — the
    no-overwrite rule enforced by the query rather than by the code that consumes it. Rows with
    no items are skipped: there is nothing to estimate, and selecting them would make the
    backfill loop forever on rows it can never fill.
    """
    cur.execute(
        """
        SELECT id, summary, payload
        FROM memories
        WHERE user_id = %s AND type = 'meal' AND status = 'active'
              AND NOT (payload ? 'nutrition')
              AND jsonb_array_length(COALESCE(payload -> 'items', '[]'::JSONB)) > 0
        ORDER BY created_at
        LIMIT %s
        """,
        [user_id, limit],
    )
    return cur.fetchall()


def set_nutrition(
    cur: psycopg.Cursor, user_id: UUID, memory_id: UUID, nutrition: dict
) -> None:
    """Attach a computed nutrition object to a meal that had none.

    Merges into the existing payload (``||``) rather than rewriting it, and re-asserts
    ``NOT (payload ? 'nutrition')`` in the WHERE clause so a concurrent writer that filled the
    key first wins — the update simply matches nothing. Facts, summary and embedding are
    untouched by construction.
    """
    cur.execute(
        """
        UPDATE memories
        SET payload = payload || jsonb_build_object('nutrition', %s::JSONB)
        WHERE id = %s AND user_id = %s AND NOT (payload ? 'nutrition')
        """,
        [Jsonb(nutrition), memory_id, user_id],
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


# ── consolidation reads (Phase 5 M3) ──────────────────────────────────────────────────
# All user-scoped, all parameterized — including the JSONB path, which is engine-owned and
# bound rather than interpolated (same posture as engine/retrieval.py's builders).

_SQL_SERIES = """
SELECT id,
       event_time,
       (payload #>> %(path)s::TEXT[])::FLOAT8 AS value,
       confidence,
       provenance,
       payload -> 'expanded_from' ->> 'composition' AS composition,
       payload -> 'expanded_from' ->> 'assertion'   AS assertion
FROM memories
WHERE user_id = %(user_id)s
  AND type = %(type)s
  AND status = 'active'
  AND payload #>> %(path)s::TEXT[] IS NOT NULL
ORDER BY event_time, id
"""


def fetch_series(
    cur: psycopg.Cursor, user_id: UUID, memory_type: str, path: tuple[str, ...]
) -> list[dict]:
    """Every active row carrying a value for one metric, oldest first.

    Returns the raw ingredients of an ``analytics.MetricSample`` **plus** ``confidence`` and
    ``provenance``, which the sample type deliberately does not carry (it is prose- and
    metadata-free by design, I-8) but which an insight needs to inherit honestly (§4.12).

    ``expanded_from`` is projected out of the payload because the collapse is defined on it
    (§4.2): a row that came from a period assertion must be distinguishable from an observed
    point event, or 30 materialized days would read as 30 independent observations.
    """
    cur.execute(_SQL_SERIES, {"user_id": user_id, "type": memory_type, "path": list(path)})
    return cur.fetchall()


_SQL_TYPE_ROWS = """
SELECT id, event_time, confidence, provenance, payload ->> 'name' AS name
FROM memories
WHERE user_id = %(user_id)s
  AND type = %(type)s
  AND status = 'active'
ORDER BY event_time, id
"""


def fetch_type_rows(cur: psycopg.Cursor, user_id: UUID, memory_type: str) -> list[dict]:
    """Active rows of one type, oldest first — the input to structural onset detection (§4.4).

    Reduced to first-occurrences in Python rather than by ``DISTINCT ON``: the reduction is
    part of the intervention *definition*, so keeping it beside that definition (and out of
    SQL) is what lets it be unit-tested without a database.
    """
    cur.execute(_SQL_TYPE_ROWS, {"user_id": user_id, "type": memory_type})
    return cur.fetchall()


_SQL_BEHAVIOURAL_TIMES = """
SELECT DISTINCT event_time
FROM memories
WHERE user_id = %(user_id)s
  AND type = ANY(%(types)s)
  AND status = 'active'
"""


def fetch_behavioural_times(
    cur: psycopg.Cursor, user_id: UUID, types: list[str]
) -> list[datetime]:
    """Instants on which the user logged *behaviour* — the raw material for an
    ``intervention_outcome``'s coverage factor (§4.13).

    Instants, not dates: bucketing to a local day is the engine's job and depends on the
    user's timezone (ADR-14.10), which SQL here has no business deciding.
    """
    cur.execute(_SQL_BEHAVIOURAL_TIMES, {"user_id": user_id, "types": types})
    return [row["event_time"] for row in cur.fetchall()]


_SQL_ACTIVE_INSIGHT = """
SELECT id, created_at, payload
FROM memories
WHERE user_id = %(user_id)s
  AND type = 'insight'
  AND status = 'active'
  AND payload ->> 'kind' = %(kind)s
  AND payload ->> 'series_metric' = %(metric)s
ORDER BY created_at DESC, id DESC
"""


def find_active_insights(
    cur: psycopg.Cursor, user_id: UUID, kind: str, metric: str
) -> list[dict]:
    """Active insights for one identity ``(user_id, kind, series_metric)`` (§4.6).

    Returns a list although **I-10** allows at most one: the caller reconciles a surplus rather
    than this query hiding it. Silently taking the newest would let a duplicate accumulate
    invisibly, which is the failure the identity rule exists to prevent.
    """
    cur.execute(_SQL_ACTIVE_INSIGHT, {"user_id": user_id, "kind": kind, "metric": metric})
    return cur.fetchall()


_SQL_SERIES_LEARNED_AT = """
SELECT max(created_at) AS learned_at
FROM memories
WHERE user_id = %(user_id)s
  AND type = %(type)s
  AND status = 'active'
  AND payload #>> %(path)s::TEXT[] IS NOT NULL
"""


def series_learned_at(
    cur: psycopg.Cursor, user_id: UUID, memory_type: str, path: tuple[str, ...]
) -> datetime | None:
    """When the engine last *learned* anything about a series (§4.7).

    Freshness is derived from this rather than stored on the insight: ``created_at`` already
    means "when we learned it" (04's bi-temporal model), so "has anything arrived since this
    claim was derived" is the schema's own reading. Storing a ``last_evaluated_at`` would add a
    second source of truth for a derivable fact — and would make ``memories`` mutable in a way
    it currently is not (**I-13**).
    """
    cur.execute(_SQL_SERIES_LEARNED_AT, {"user_id": user_id, "type": memory_type,
                                         "path": list(path)})
    row = cur.fetchone()
    return row["learned_at"] if row else None


# ── retraction (Phase 5 M4, T5) ───────────────────────────────────────────────────────

_SQL_SERIES_WINDOW = _SQL_SERIES.replace(
    "ORDER BY event_time, id",
    "  AND event_time > %(start)s\n  AND event_time <= %(end)s\nORDER BY event_time, id",
)


def fetch_series_window(
    cur: psycopg.Cursor,
    user_id: UUID,
    memory_type: str,
    path: tuple[str, ...],
    start: datetime,
    end: datetime,
    ) -> list[dict]:
    """``fetch_series`` bounded to ``(start, end]`` — a retraction condition only ever looks at
    a trailing window, so it must not drag a lifetime of rows across the wire to examine a
    month. Derived from the same statement so the two cannot drift apart."""
    cur.execute(
        _SQL_SERIES_WINDOW,
        {"user_id": user_id, "type": memory_type, "path": list(path),
         "start": start, "end": end},
    )
    return cur.fetchall()


_SQL_RETRACTABLE = """
SELECT id, created_at, payload
FROM memories
WHERE user_id = %(user_id)s
  AND type = 'insight'
  AND status = 'active'
  AND jsonb_typeof(payload -> 'retraction_condition') = 'object'
ORDER BY created_at, id
"""


def fetch_retractable_insights(cur: psycopg.Cursor, user_id: UUID) -> list[dict]:
    """Active insights that carry a retraction condition — the only rows M4 may act on.

    An insight without a condition is not "safe", it is simply *unfalsifiable by this
    mechanism*, and must be left alone rather than judged by some default rule.

    The filter tests ``jsonb_typeof(...) = 'object'`` rather than ``IS NOT NULL``: a JSON
    ``null`` is not SQL ``NULL``, so the simpler predicate lets an explicitly-null condition
    through to be rejected later as unreadable. ``payload_to_json`` drops ``None`` fields, so
    production rows omit the key entirely and both predicates agree — but a hand-written or
    hand-repaired payload is exactly where the difference would show up, and "no condition"
    must mean the same thing however it was written."""
    cur.execute(_SQL_RETRACTABLE, {"user_id": user_id})
    return cur.fetchall()


def mark_retracted(cur: psycopg.Cursor, user_id: UUID, memory_id: UUID) -> None:
    """Flip an insight to ``status='retracted'`` (ADR-9, **I-20**).

    Retraction is **not** supersession, and this is deliberately not ``mark_superseded``:
    nothing replaces a retracted claim, so ``superseded_by`` stays NULL and the two mechanisms
    04's model distinguishes stay distinguishable in the data. The statement touches ``status``
    and nothing else — the payload is never rewritten, so what the engine claimed, and the
    condition it agreed to be judged by, remain exactly as written. Scoped, and only active
    rows, so re-running it is a no-op rather than a second flip.
    """
    cur.execute(
        "UPDATE memories SET status = 'retracted' "
        "WHERE id = %s AND user_id = %s AND status = 'active'",
        [memory_id, user_id],
    )


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


def latest_weight(cur: psycopg.Cursor, user_id: UUID) -> dict | None:
    """The user's most recent active ``weight`` memory, or ``None`` if they have never logged
    one. This is the *only* definition of "current weight" the profile/target calculator uses
    (ADR-17.2) — there is no cached weight column on ``user_profile`` to drift from it."""
    cur.execute(
        """
        SELECT id, weight_kg, event_time
        FROM (
            SELECT id, (payload ->> 'weight_kg')::FLOAT AS weight_kg, event_time
            FROM memories
            WHERE user_id = %s AND type = 'weight' AND status = 'active'
        ) AS w
        WHERE weight_kg IS NOT NULL
        ORDER BY event_time DESC
        LIMIT 1
        """,
        [user_id],
    )
    return cur.fetchone()
