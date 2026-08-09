"""Read queries behind the glass box (M3 / T7 · T16, glass-box-architecture.md §4.5–4.6).

Read-only and user-scoped, every one of them. This module never writes; stage (G)
(``engine/turns.py``) is the only thing that writes ``turns``/``evidence_traces``.

**The trace is served verbatim (I-29).** ``fetch_trace`` returns the stored JSONB unchanged —
no re-derivation, no recomputation, no merging with live data. A rendered glass box that can
drift from what the turn actually did is worse than no glass box, because it looks
authoritative while being wrong.

**Hydration is batched (T16, §4.6).** ``fetch_memories`` resolves a whole set of IDs in one
query. This is where ADR-14.7's deferred work lands: assembly is pure, so an aggregate's
contributing rows are IDs without metadata, and the UI hydrates them on demand. Cross-region
round trips dominate this system — Phase 5 measured ~635 ms per consolidation series against
the ap-south-1 cluster — so N+1 at the API boundary is not a style preference here, it is the
difference between a responsive evidence pane and an unusable one.

**Scoping (I-28).** Every query filters on ``user_id``, and a row belonging to someone else is
indistinguishable from a row that does not exist — the same posture ``repository.get_memory``
takes, and what makes cross-user probing a dead end.
"""

from __future__ import annotations

from uuid import UUID

import psycopg

#: Display columns for a hydrated evidence row. Payload is included here (unlike
#: ``EvidenceSnapshot``, which is deliberately payload-free): a snapshot travels inside the
#: trace where copies would duplicate truth, whereas this is the UI resolving a chip against
#: the live row and the payload is the thing the user actually wants to see.
_ROW_COLS = (
    "id, event_time, tz, type, source, provenance, confidence, "
    "status, superseded_by, summary, payload, created_at"
)

#: A conversation page. Kept small: the UI pages history rather than loading a year of chat.
MAX_TURNS_PAGE = 100

#: One batch hydration. Bounded so a crafted request cannot ask for the whole table; the UI
#: never needs more than a turn's worth of chips at once.
MAX_BATCH = 200

#: A thread list page (T-sidebar). Small for the same reason MAX_TURNS_PAGE is: a sidebar
#: shows recent conversations, not a full account history.
MAX_THREADS_PAGE = 50


def fetch_trace(cur: psycopg.Cursor, user_id: UUID, turn_id: UUID) -> dict | None:
    """The persisted trace for a turn, plus the answer it explains.

    Returns ``None`` when the turn does not exist, has no trace, or belongs to someone else —
    all indistinguishable by design (I-28).

    The answer travels with the trace because the citation report is recomputed from the two
    together (§4.4): the report is a pure function of the answer and ``trace.citable_ids``, so
    it is derived on read rather than stored, and cannot drift from its inputs.
    """
    cur.execute(
        """
        SELECT e.trace, e.created_at, t.content AS answer, t.thread_id, t.memory_ids
        FROM evidence_traces e
        JOIN turns t ON t.id = e.turn_id AND t.user_id = e.user_id
        WHERE e.turn_id = %(turn_id)s AND e.user_id = %(user_id)s
        """,
        {"turn_id": turn_id, "user_id": user_id},
    )
    return cur.fetchone()


def fetch_turns(
    cur: psycopg.Cursor, user_id: UUID, *, thread_id: str | None = None, limit: int = 50
) -> list[dict]:
    """One page of conversation history, oldest last.

    ``has_trace`` is computed rather than joined-and-hydrated so the list stays cheap: the UI
    needs to know *whether* a turn has a glass box before deciding to fetch it, not what is in
    it. A turn without one is honest — stage (G) is best-effort (§4.3).
    """
    limit = max(1, min(limit, MAX_TURNS_PAGE))
    cur.execute(
        """
        SELECT t.id, t.role, t.content, t.thread_id, t.memory_ids, t.created_at,
               (e.id IS NOT NULL) AS has_trace
        FROM turns t
        LEFT JOIN evidence_traces e ON e.turn_id = t.id AND e.user_id = t.user_id
        WHERE t.user_id = %(user_id)s
          AND (%(thread_id)s::TEXT IS NULL OR t.thread_id = %(thread_id)s)
        ORDER BY t.created_at DESC, t.role
        LIMIT %(limit)s
        """,
        {"user_id": user_id, "thread_id": thread_id, "limit": limit},
    )
    return list(reversed(cur.fetchall()))


def fetch_threads(cur: psycopg.Cursor, user_id: UUID, *, limit: int = 20) -> list[dict]:
    """Recent conversation threads, most recently active first (the sidebar's data).

    ``preview`` is the thread's *first* user message, verbatim — never an LLM-generated title.
    Rule 16 (no component derives structured data from model output) is about the frontend, but
    the same instinct applies here: a summary would need to be computed and stored somewhere,
    and would drift from the conversation it claims to label. The first message a person typed
    cannot drift from itself.

    Two passes rather than one `DISTINCT ON` sorted by recency directly, because `DISTINCT ON`
    requires its `ORDER BY` to start with the distinct columns — sorting the *output* by
    recency needs a second pass over the deduplicated set. ``thread_id`` here is still the
    namespaced ``user_id:client_id`` form (see ``thread_key``); the router strips it, the same
    split as it would have to do regardless of which query produced the value.
    """
    limit = max(1, min(limit, MAX_THREADS_PAGE))
    cur.execute(
        """
        SELECT first_user_msg.thread_id, first_user_msg.preview, recency.last_message_at
        FROM (
          SELECT DISTINCT ON (t.thread_id)
            t.thread_id,
            t.content AS preview,
            t.created_at
          FROM turns t
          WHERE t.user_id = %(user_id)s AND t.role = 'user'
          ORDER BY t.thread_id, t.created_at ASC
        ) first_user_msg
        JOIN (
          SELECT thread_id, max(created_at) AS last_message_at
          FROM turns
          WHERE user_id = %(user_id)s
          GROUP BY thread_id
        ) recency USING (thread_id)
        ORDER BY last_message_at DESC
        LIMIT %(limit)s
        """,
        {"user_id": user_id, "limit": limit},
    )
    return cur.fetchall()


def fetch_memories(cur: psycopg.Cursor, user_id: UUID, ids: list[UUID]) -> list[dict]:
    """Hydrate a set of memory IDs in **one** query (T16).

    Missing IDs are simply absent from the result rather than raising: a trace can legitimately
    cite a row that was later superseded or removed, and the pane should render what it can and
    say so, not fail the whole panel over one chip.
    """
    if not ids:
        return []
    cur.execute(
        f"SELECT {_ROW_COLS} FROM memories WHERE user_id = %(user_id)s AND id = ANY(%(ids)s)",
        {"user_id": user_id, "ids": ids[:MAX_BATCH]},
    )
    return cur.fetchall()


def fetch_stats(cur: psycopg.Cursor, user_id: UUID) -> dict:
    """The top-bar numbers: "4,182 memories · 312 days · 23 insights".

    Zeros are the honest answer for a new account, not an error — every account starts empty
    (ADR-13.4), so this must return a well-formed shape for a user with nothing logged. That is
    what lets T11's empty states read as *inviting* rather than broken.
    """
    cur.execute(
        """
        SELECT
          count(*) FILTER (WHERE type <> 'insight')                       AS memories,
          count(*) FILTER (WHERE type = 'insight' AND status = 'active')  AS insights,
          count(DISTINCT date_trunc('day', event_time))                   AS days,
          min(event_time)                                                 AS first_event,
          max(event_time)                                                 AS last_event
        FROM memories
        WHERE user_id = %(user_id)s AND status <> 'superseded'
        """,
        {"user_id": user_id},
    )
    return cur.fetchone()


def fetch_timeline(cur: psycopg.Cursor, user_id: UUID) -> list[dict]:
    """Memory density per day, for the timeline strip.

    Aggregated in SQL rather than by counting rows in Python: the strip covers the full
    history, and shipping a year of rows to the client to count them would be the N+1 mistake
    in a different costume.
    """
    cur.execute(
        """
        SELECT date_trunc('day', event_time)::DATE AS day,
               count(*)                            AS n,
               count(*) FILTER (WHERE type = 'insight') AS insights
        FROM memories
        WHERE user_id = %(user_id)s AND status <> 'superseded'
        GROUP BY 1
        ORDER BY 1
        """,
        {"user_id": user_id},
    )
    return cur.fetchall()
