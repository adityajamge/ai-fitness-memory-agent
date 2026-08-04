"""The Phase 2 ingestion write path (T4 + T15).

Implements docs/engineering/ingestion-transaction-boundaries.md exactly — read that doc for
the *why*; this module is the *how*. The load-bearing rules, restated in one breath: all
model work (extract, embed) happens OUTSIDE any transaction; a turn commits its typed events
atomically or falls back to a single note; input is never lost given a reachable database;
embeddings are a nullable enrichment backfilled later.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from engine.consolidation import ConsolidationService
from engine.db import Database
from engine.memory import Memory
from engine.model import EmbeddingError, ExtractedEvent, ExtractionError, ModelProvider
from engine.repository import (
    fetch_unembedded,
    get_memory,
    get_note,
    insert_memories,
    mark_superseded,
    set_embedding,
)
from engine.types import UnknownMemoryType, ValidationError, payload_to_json, validate_payload

logger = logging.getLogger(__name__)

_NOTE_CONFIDENCE = 1.0  # we're certain the user said it; only the *parse* is incomplete
_MSG_OK = "saved"
_MSG_NOOP = "nothing to log"
_MSG_INCOMPLETE = "saved — parsing incomplete"


@dataclass(frozen=True)
class MemoryRef:
    id: UUID
    type: str
    summary: str | None
    embedding_pending: bool


@dataclass(frozen=True)
class Receipt:
    """The miniature evidence trace of what a turn created (ADR-12: 'ingestion receipts are
    the same artifact in miniature'). Full trace persistence is T7/Phase 6."""

    created: list[MemoryRef] = field(default_factory=list)
    parse_status: Literal["ok", "incomplete"] = "ok"
    message: str = _MSG_OK
    superseded_note_id: UUID | None = None
    superseded_ids: list[UUID] = field(default_factory=list)
    #: Insights stage (F₀) derived from this turn (Phase 5 §4.8). Separate from ``created``
    #: because they are a different tier of memory: the user reported the events in
    #: ``created``, while these are claims the engine made *about* them. Collapsing the two
    #: would let a receipt imply the user logged something they never said.
    insights: list[MemoryRef] = field(default_factory=list)


class IngestionService:
    def __init__(
        self,
        db: Database,
        model: ModelProvider,
        *,
        default_tz: str,
        backfill_batch: int = 32,
        consolidation: ConsolidationService | None = None,
    ) -> None:
        self.db = db
        self.model = model
        self.default_tz = default_tz
        self.backfill_batch = backfill_batch
        #: Stage (F₀). Optional so the write path stands alone — every pre-Phase-5 caller and
        #: test constructs this service without one and behaves exactly as before.
        self.consolidation = consolidation

    # ── public API ────────────────────────────────────────────────────────────────────
    def ingest_text(
        self,
        user_id: UUID,
        text: str,
        *,
        source: str = "chat",
        provenance: str = "live",
        now: datetime | None = None,
        tz: str | None = None,
    ) -> Receipt:
        now = now or datetime.now(timezone.utc)
        tz = tz or self.default_tz

        # (A) extraction with one inline retry — off-transaction
        try:
            events = self._extract_with_retry(text, now=now, tz=tz)
        except ExtractionError:
            logger.info("extraction failed for user %s; falling back to note", user_id)
            return self._persist_note(user_id, text, source, provenance, now, tz)

        # Empty result means the provider judged the turn contentless (see model.py contract);
        # contentful-but-unparseable input raises ExtractionError instead and is handled above.
        if not events:
            return Receipt(parse_status="ok", message=_MSG_NOOP)

        # (B) validation — all-or-nothing per turn (transaction-boundaries doc §5)
        try:
            memories = self._build_memories(user_id, events, source, provenance)
        except (ValidationError, UnknownMemoryType) as exc:
            logger.info("validation failed for user %s (%s); note fallback", user_id, exc)
            return self._persist_note(user_id, text, source, provenance, now, tz)

        return self._persist_validated(user_id, memories)

    def ingest_events(
        self,
        user_id: UUID,
        events: list[ExtractedEvent],
        *,
        source: str = "replay",
        provenance: str = "reconstructed",
    ) -> Receipt:
        """Direct-ingest entry point (§4.11): skips extraction (stage A) entirely and shares
        stage B onward with ``ingest_text``. ``events`` are already typed — produced by a
        dev-time tool, not inferred by a model at request time.

        Unlike ``ingest_text``, validation failure here is **fatal**: it raises instead of
        falling back to a note. A bad payload on this path means the caller emitted invalid
        data, not that a user's phrasing was ambiguous — there is no user turn to preserve
        as a note fallback for.
        """
        if not events:
            raise ValueError("ingest_events requires at least one event")

        # (B) validation — raises on failure; caller's responsibility, not a note case
        memories = self._build_memories(user_id, events, source, provenance)

        return self._persist_validated(user_id, memories)

    def ingest_events_superseding(
        self,
        user_id: UUID,
        events: list[ExtractedEvent],
        superseded_ids: list[UUID],
        *,
        source: str = "replay",
        provenance: str = "reconstructed",
    ) -> Receipt:
        """Direct-ingest + supersession, in one transaction (§4.14) — the correction-workflow
        counterpart to ``ingest_events``. Every corrected replay record inserts its
        replacement and retires the ledger's previously-committed rows atomically, so no
        intermediate state (new row active AND old row still active, or vice versa) is ever
        observable.

        This mirrors ``reprocess_note``'s shape (insert the replacement, retire the original,
        one transaction) rather than sharing ``_persist_validated``'s (C)-(F) tail, for the
        same reason ``reprocess_note`` already can't: the transaction body here also
        supersedes, so it isn't that tail's shape. Two transaction shapes exist in this
        module — the shared tail, and insert+supersede — not four independent ones; every
        entry point still funnels through exactly one of them (§4.14).

        Like ``ingest_events``, validation failure is fatal: there is no note fallback on the
        direct-ingest path (§4.11) — a bad payload here means the caller (replay) emitted bad
        data, and nothing is written.
        """
        if not events:
            raise ValueError("ingest_events_superseding requires at least one event")
        if not superseded_ids:
            raise ValueError("ingest_events_superseding requires at least one superseded id")

        # (B) validation — raises on failure; caller's responsibility, not a note case
        memories = self._build_memories(user_id, events, source, provenance)

        # (C) embeddings — nullable, off-transaction
        self._attach_embeddings(memories)

        # (D) insert the replacement(s) + retire the originals, one transaction
        with self.db.transaction() as cur:
            ids = insert_memories(cur, memories)
            for old_id in superseded_ids:
                mark_superseded(cur, user_id, old_id, superseded_by=ids[0])
        for m, mid in zip(memories, ids, strict=True):
            m.id = mid

        # (E) receipt from committed rows, then (F) opportunistic backfill
        receipt = Receipt(
            created=self._refs(memories),
            parse_status="ok",
            message=_MSG_OK,
            superseded_ids=list(superseded_ids),
        )
        self._opportunistic_backfill(user_id)
        return receipt

    def reprocess_note(self, user_id: UUID, note_id: UUID) -> Receipt:
        """Upgrade an existing note into typed events; on success supersede the note in one
        transaction (transaction-boundaries doc §8). On any failure the note is left
        untouched and active — nothing lost, nothing duplicated.

        The new typed events inherit the note's own ``source`` and ``provenance``: upgrading
        a parse is not a change of origin, so a reconstructed note yields reconstructed
        memories."""
        with self.db.transaction() as cur:
            note = get_note(cur, user_id, note_id)
        # `text` is NOT NULL for every note we write (NotePayload requires it); the guard
        # keeps a hand-edited row from reaching extraction as None.
        if note is None or note["text"] is None:
            raise ValueError(f"no active note {note_id} for user {user_id}")
        text = note["text"]

        now = datetime.now(timezone.utc)
        try:
            events = self._extract_with_retry(text, now=now, tz=self.default_tz)
            if not events:
                raise ExtractionError("empty re-extraction")
            memories = self._build_memories(
                user_id, events, source=note["source"], provenance=note["provenance"]
            )
        except (ExtractionError, ValidationError, UnknownMemoryType):
            logger.info("reprocess of note %s still fails; leaving it active", note_id)
            return Receipt(parse_status="incomplete", message=_MSG_INCOMPLETE)

        self._attach_embeddings(memories)
        with self.db.transaction() as cur:
            ids = insert_memories(cur, memories)
            mark_superseded(cur, user_id, note_id, superseded_by=ids[0])
        for m, mid in zip(memories, ids, strict=True):
            m.id = mid

        receipt = Receipt(
            created=self._refs(memories),
            parse_status="ok",
            message=_MSG_OK,
            superseded_note_id=note_id,
        )
        self._opportunistic_backfill(user_id)
        return receipt

    def backfill_embeddings(self, user_id: UUID, limit: int | None = None) -> int:
        """Embed up to ``limit`` of the user's NULL-embedding rows (T15). Returns the count
        embedded. Each row's UPDATE is its own short transaction so one failure doesn't undo
        the rest (transaction-boundaries doc §7). Embedding itself is off-transaction."""
        limit = limit or self.backfill_batch
        with self.db.transaction() as cur:
            rows = fetch_unembedded(cur, user_id, limit)
        if not rows:
            return 0

        try:
            vectors = self.model.embed([r["summary"] for r in rows])
        except EmbeddingError:
            logger.warning("backfill embed call failed for user %s; will retry later", user_id)
            return 0

        embedded = 0
        for row, vec in zip(rows, vectors, strict=True):
            with self.db.transaction() as cur:
                set_embedding(cur, user_id, row["id"], vec)
            embedded += 1
        return embedded

    # ── internals ─────────────────────────────────────────────────────────────────────
    def _extract_with_retry(
        self, text: str, *, now: datetime, tz: str
    ) -> list[ExtractedEvent]:
        try:
            return self.model.extract_events(text, now=now, tz=tz)
        except ExtractionError:
            logger.info("extraction failed; one inline retry")
            return self.model.extract_events(text, now=now, tz=tz)  # may raise -> caller notes

    def _build_memories(
        self, user_id: UUID, events: list[ExtractedEvent], source: str, provenance: str
    ) -> list[Memory]:
        memories: list[Memory] = []
        for ev in events:
            validated = validate_payload(ev.type, ev.payload)  # raises -> caller note-fallback
            memories.append(
                Memory(
                    user_id=user_id,
                    event_time=ev.event_time,
                    tz=ev.tz,
                    type=ev.type,
                    source=source,
                    provenance=provenance,
                    confidence=ev.confidence,
                    summary=ev.summary,
                    payload=payload_to_json(validated),
                )
            )
        return memories

    def _persist_validated(self, user_id: UUID, memories: list[Memory]) -> Receipt:
        """Shared (C)-(F) tail: embed, commit in one transaction, build the receipt from
        committed rows, then opportunistic backfill. ``memories`` must already be validated
        (stage B) — this stage never rejects input, only persists it."""
        # (C) embeddings — nullable, off-transaction
        self._attach_embeddings(memories)

        # (D) single write transaction
        with self.db.transaction() as cur:
            ids = insert_memories(cur, memories)
        for m, mid in zip(memories, ids, strict=True):
            m.id = mid

        # (E) receipt from committed rows, (F₀) consolidation, then (F₁) backfill
        receipt = Receipt(created=self._refs(memories), parse_status="ok", message=_MSG_OK)
        receipt = self._consolidate(user_id, memories, receipt)
        self._opportunistic_backfill(user_id)
        return receipt

    def _attach_embeddings(self, memories: list[Memory]) -> None:
        indexed = [(i, m) for i, m in enumerate(memories) if m.summary]
        if not indexed:
            return
        try:
            vectors = self.model.embed([m.summary for _, m in indexed])
        except EmbeddingError:
            logger.warning("embedding failed; %d rows -> NULL, backfill pending", len(indexed))
            return
        for (_, m), vec in zip(indexed, vectors, strict=True):
            m.embedding = vec

    def _persist_note(
        self, user_id: UUID, text: str, source: str, provenance: str, now: datetime, tz: str
    ) -> Receipt:
        """Write the raw input as a single note, preserving the turn's own ``source`` and
        ``provenance``. A note is a *representation of the same turn*, so it must not be
        relabelled: a failed parse during replay stays ``reconstructed``, never ``live``."""
        note = Memory(
            user_id=user_id,
            event_time=now,
            tz=tz,
            type="note",
            source=source,
            provenance=provenance,
            confidence=_NOTE_CONFIDENCE,
            summary=text,
            payload={"text": text},
        )
        self._attach_embeddings([note])
        with self.db.transaction() as cur:
            (note.id,) = insert_memories(cur, [note])
        receipt = Receipt(
            created=self._refs([note]), parse_status="incomplete", message=_MSG_INCOMPLETE
        )
        self._opportunistic_backfill(user_id)
        return receipt

    def _consolidate(self, user_id: UUID, memories: list[Memory], receipt: Receipt) -> Receipt:
        """Stage (F₀) — derive insights from what this turn just committed (§4.8).

        **Post-commit, best-effort, budgeted.** It runs after (D) has committed and after the
        receipt has been built from committed rows, in its own transactions. Three consequences,
        each deliberate:

        * It can never roll back the user's input. Running it before the commit would let a
          *derived*-data failure lose a fact the user reported — inverting never-lose-input for
          the sake of a hypothesis.
        * It is outside the turn's write transaction (**I-14**), so transaction-boundaries
          rule 1 still holds: no network call happens while a transaction is open.
        * A failure here **never fails the turn** (**I-15**) — the same posture as backfill.
          An insight lost to an error costs one re-derivation, because the next ingest touching
          the series recomputes it and the identity rule makes that idempotent.

        Returns the receipt, extended with any insights created, so the caller sees one
        artifact rather than having to ask twice.
        """
        if self.consolidation is None:
            return receipt
        try:
            outcome = self.consolidation.consolidate_touched(user_id, memories)
        except Exception:  # noqa: BLE001 — consolidation must never break a committed turn
            logger.exception("consolidation failed for user %s (swallowed)", user_id)
            return receipt
        if not outcome.created_ids:
            return receipt
        return replace(receipt, insights=self._insight_refs(user_id, outcome.created_ids))

    def _insight_refs(self, user_id: UUID, insight_ids: list[UUID]) -> list[MemoryRef]:
        """Read back the insights just written so the receipt describes committed rows, not
        what the service intended to write (transaction-boundaries rule 4)."""
        refs: list[MemoryRef] = []
        with self.db.transaction() as cur:
            for insight_id in insight_ids:
                row = get_memory(cur, user_id, insight_id)
                if row is None:  # pragma: no cover — it was committed a moment ago
                    continue
                refs.append(
                    MemoryRef(
                        id=row["id"],
                        type=row["type"],
                        summary=row["summary"],
                        embedding_pending=not row["has_embedding"],
                    )
                )
        return refs

    def _opportunistic_backfill(self, user_id: UUID) -> None:
        """Best-effort: a backfill failure never surfaces as a turn failure (doc §7)."""
        try:
            self.backfill_embeddings(user_id, self.backfill_batch)
        except Exception:  # noqa: BLE001 — backfill must never break a completed turn
            logger.exception("opportunistic backfill failed for user %s (swallowed)", user_id)

    @staticmethod
    def _refs(memories: list[Memory]) -> list[MemoryRef]:
        return [
            MemoryRef(
                id=m.id,
                type=m.type,
                summary=m.summary,
                embedding_pending=(m.summary is not None and m.embedding is None),
            )
            for m in memories
        ]
