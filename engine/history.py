"""Short-term conversation memory: the recent turns of one thread (ADR-14.16).

**The two memories, and why this module is small.** Long-term memory is the product — typed
rows in ``memories``, retrieved deterministically, cited by id, shown in the glass box. Short-
term memory is *just the conversation*: what was said a moment ago, so "how did you find
that?" resolves to something. This module is the whole of the second one, and it is a read
over a table that has been written on every turn since Phase 6 (``engine/turns.py``).

**Why ``turns`` and not the checkpointer.** LangGraph already accumulates a ``messages``
channel per thread, and it was tempting to read that back. Four reasons not to, the third
decisive:

  1. ADR-13.14 assigns the roles — ``turns`` is read/UI truth, the checkpoint is execution
     state. Sourcing prompt content from the checkpoint blurs a boundary this codebase
     enforces at the serialization layer (``agent/checkpointer.py``).
  2. ``turns`` takes a ``LIMIT``. The ``messages`` channel is one unbounded blob that must be
     deserialized whole, and it grows for the life of the thread.
  3. **``POST /api/chat/photo`` never runs the graph.** It ingests, assembles and persists
     directly (``api/routers/chat.py``), so photo turns exist in ``turns`` and are *absent*
     from ``messages``. Reading the checkpoint would make the assistant blind to every photo
     the user sent — "what was in that photo?" would stay broken.
  4. Roles and ordering are explicit columns here, not inferred from message classes.

**What this module must never become.** History is context. It is not evidence and it is not
an ingestion source. Nothing here returns memory ids, and no caller may pass its output to
``IngestionService`` or into a ``ContextBlock`` — the ingestion path takes ``state["question"]``
alone (ADR-14.15), and ``citable_ids`` is computed from retrieved rows alone. Both are enforced
elsewhere by construction; this docstring exists so a future reader knows the omissions are
deliberate rather than unfinished.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import psycopg

from engine.citations import CITATION_RE
from engine.model import HistoryTurn

#: How many messages of context the planner and narrator see. Twelve rows is roughly six
#: exchanges — the horizon over which pronouns and follow-ups actually resolve. Past that,
#: recalling something is *long-term* memory's job, which is the product; feeding a longer
#: transcript would spend latency on every turn to make the planner's tool selection worse.
DEFAULT_MAX_TURNS = 12

#: Total character budget across all history. Characters, not tokens, deliberately: no
#: tokenizer is in the dependency tree, the two providers tokenize differently, and a
#: character budget is deterministic and testable — the same posture assembly takes with
#: ``_DEFAULT_MAX_MEMORIES``. Oldest messages are dropped first when the budget binds.
DEFAULT_MAX_CHARS = 8000

#: Per-message cap, so one pasted wall of text cannot consume the whole window and push out
#: every other turn. A truncated message keeps its head: the start of a turn is what carries
#: the topic, and the topic is what history is for.
DEFAULT_MAX_TURN_CHARS = 2000

_TRUNCATION_MARK = "…[truncated]"


def scrub_citations(text: str) -> str:
    """Strip ``[memory-id]`` markers from a stored assistant answer.

    Historical answers are full of them, and feeding them back verbatim invites the model to
    reuse an id for a claim *this* turn's evidence does not support. ``validate_citations``
    would then correctly mark the answer invalid, and the user would see a broken glass box
    for a mistake that originated in our own prompt. The markers carry no conversational
    meaning — they are machine annotations — so removing them costs the model nothing.

    Only the id is removed, never the prose around it, so the sentence still reads normally.
    """
    return _collapse_spaces(CITATION_RE.sub("", text))


def _collapse_spaces(text: str) -> str:
    """Tidy the double spaces and space-before-punctuation that removing an inline marker
    leaves behind. Cosmetic, but a prompt full of ``you ate eggs  .`` is a prompt that teaches
    the model to write that way."""
    out = " ".join(text.split())
    for punct in (" .", " ,", " ;", " :", " !", " ?", " )"):
        out = out.replace(punct, punct[1])
    return out.replace("( ", "(")


def _zone(tz: str) -> ZoneInfo | None:
    try:
        return ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        return None


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit].rstrip() + _TRUNCATION_MARK


def fetch_history(
    cur: psycopg.Cursor,
    user_id: UUID,
    thread_id: str,
    *,
    tz: str,
    max_turns: int = DEFAULT_MAX_TURNS,
    max_chars: int = DEFAULT_MAX_CHARS,
    max_turn_chars: int = DEFAULT_MAX_TURN_CHARS,
) -> list[HistoryTurn]:
    """Recent messages from one thread, oldest first, budgeted and citation-scrubbed.

    ``thread_id`` is the **namespaced** ``user_id:client_id`` form (``api.routers.chat``'s
    ``thread_key``) — the same key stage (G) writes. The query filters on ``user_id`` as well,
    which is redundant given that namespacing and deliberately so: it is the index's leading
    column, and I-28's scoping rule is not something to leave resting on a string prefix.

    **The current turn is never included, structurally.** Stage (G) persists a turn at the
    *end* of the graph (``persist_node``), while this is read at the *start* — so at the moment
    of the call, the current question has no row to find. Nothing filters it out because
    nothing needs to.

    Budgets apply newest-first: the most recent messages are the ones that resolve a reference,
    so when the character budget binds it is the oldest that fall away. The result is still
    returned oldest-first, which is the order a conversation is read in.

    The ``role`` tiebreak matters and is not decorative: stage (G) writes both rows of a turn
    in one transaction, so they share a ``created_at`` (CockroachDB's ``now()`` is the
    transaction timestamp). Ascending role puts ``assistant`` before ``user`` in the
    newest-first scan, which reverses into the correct ``user`` → ``assistant`` reading order.
    ``engine/glassbox.fetch_turns`` orders identically, for the same reason.
    """
    max_turns = max(0, min(max_turns, 100))
    if max_turns == 0:
        return []

    cur.execute(
        """
        SELECT role, content, created_at
        FROM turns
        WHERE user_id = %(user_id)s AND thread_id = %(thread_id)s
        ORDER BY created_at DESC, role
        LIMIT %(limit)s
        """,
        {"user_id": user_id, "thread_id": thread_id, "limit": max_turns},
    )
    rows = cur.fetchall()  # newest first — the order the budget is spent in

    zone = _zone(tz)
    collected: list[HistoryTurn] = []
    used = 0
    for row in rows:
        content = (row["content"] or "").strip()
        if row["role"] == "assistant":
            content = scrub_citations(content)
        if not content:
            continue  # an empty turn carries no context; spend the budget on one that does
        content = _clip(content, max_turn_chars)
        if used + len(content) > max_chars:
            break  # budget exhausted: everything older than this is out of the window
        used += len(content)
        at: datetime = row["created_at"]
        collected.append(
            HistoryTurn(
                role=row["role"],
                content=content,
                at=at.astimezone(zone) if zone else at,
            )
        )
    return list(reversed(collected))
