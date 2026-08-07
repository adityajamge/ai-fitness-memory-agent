"""Stage (G): persisting a completed turn and its EvidenceTrace.

**Why this is a separate module from repository.py.** That module's docstring makes a
security promise about the ``memories`` table specifically; these writes touch ``turns`` and
``evidence_traces`` and never ``memories``. Keeping them apart keeps I-26 legible — stage (G)
cannot grow a memory write by accident — and leaves repository.py's invariant about exactly
one table.

**Where (G) sits, and why it is not inside the ingestion transaction.** See
docs/engineering/glass-box-architecture.md §4.3. In short: the trace is produced two graph
nodes *after* the memories commit, and the citations do not exist until after narration, so
honouring the original "same transaction as the memories" wording (ADR-13.14,
ingestion-transaction-boundaries.md §12 — both amended) would mean holding the transaction
that guarantees never-lose-input open across an LLM call.

**What (G) does guarantee (I-25):** the turn rows and the trace row are atomic *with each
other*. Never a turn whose trace is missing, never a trace pointing at a turn that does not
exist. They are deliberately **not** atomic with the memories, which committed earlier and
stand regardless.

The security invariant from repository.py applies here unchanged: every statement is
parameterized and scoped by ``user_id``.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from engine.trace import EvidenceTrace


@dataclass(frozen=True, slots=True)
class TurnRecord:
    """What stage (G) wrote — the handles the API needs to serve the turn back.

    ``assistant_turn_id`` is the one the trace hangs off and the one the glass-box UI reads
    by; the user turn is stored so the conversation can be replayed, but carries no trace of
    its own (there is nothing deterministic to show for "the user typed this").
    """

    user_turn_id: UUID
    assistant_turn_id: UUID
    trace_id: UUID


def persist_turn(
    cur: psycopg.Cursor,
    *,
    user_id: UUID,
    thread_id: str | None,
    question: str,
    answer: str,
    memory_ids: list[UUID] | None = None,
    trace: EvidenceTrace,
) -> TurnRecord:
    """Write the user turn, the assistant turn, and the trace. Call inside a transaction.

    Both turn rows and the trace row must land together — the caller's ``with
    db.transaction()`` is what makes that true, and this function must therefore never be
    called outside one. ``memory_ids`` are the memories this turn created (ingest turns) or
    referenced; they are already committed by stage (D), and are recorded here by id only.

    The trace is stored as ``trace.to_json()`` **verbatim** (I-29): the read API serves what
    was stored, and re-deriving a trace at read time would let the rendered glass box drift
    from what the turn actually did.
    """
    cur.execute(
        """
        INSERT INTO turns (user_id, thread_id, role, content, memory_ids)
        VALUES (%s, %s, 'user', %s, %s)
        RETURNING id
        """,
        [user_id, thread_id, question, []],
    )
    user_turn_id = cur.fetchone()["id"]

    cur.execute(
        """
        INSERT INTO turns (user_id, thread_id, role, content, memory_ids)
        VALUES (%s, %s, 'assistant', %s, %s)
        RETURNING id
        """,
        [user_id, thread_id, answer, list(memory_ids or [])],
    )
    assistant_turn_id = cur.fetchone()["id"]

    cur.execute(
        """
        INSERT INTO evidence_traces (turn_id, user_id, trace)
        VALUES (%s, %s, %s)
        RETURNING id
        """,
        [assistant_turn_id, user_id, Jsonb(trace.to_json())],
    )
    trace_row_id = cur.fetchone()["id"]

    return TurnRecord(
        user_turn_id=user_turn_id,
        assistant_turn_id=assistant_turn_id,
        trace_id=trace_row_id,
    )
