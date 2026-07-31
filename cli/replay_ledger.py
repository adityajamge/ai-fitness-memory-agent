"""The idempotent replay resume ledger (T8 M2) — the P0 defense.

Design: docs/engineering/replay-architecture.md §4.3 (resume), §4.12 (corrections).

The write path has **no deduplication by design** (`ingestion-transaction-boundaries.md` §10):
submitting the same content twice produces two rows. That is correct for live chat and fatal
for a bulk re-run, so the entire protection against duplicating a 424-record history lives
here. Duplicates would silently inflate the aggregates the demo's causal story rests on — a
wrong answer that looks right, which is the failure class this whole phase is shaped around.

**The invariant everything rests on:** an entry is appended **strictly after** the ingest call
returns. Never before, never batched. A crash between the commit and the ledger write costs one
re-processed record; a crash the other way round would mark work that never committed and skip
it forever. Same ordering rule as `ingestion-transaction-boundaries.md` rule 4 ("the receipt is
derived from what actually committed"), applied to a CLI-owned durability record.

Deliberately **file-only except for `rebuild_from_db`**: the ledger is not application state
and must survive with no database available. M4 wires it into the loop; M2 keeps it an isolated,
exhaustively tested unit so that when the forced-double-run test fails, the bug is provably in
the wiring rather than the primitive.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg

from cli.replay_dataset import DatasetError, ReplayRecord, dumps_canonical

__all__ = [
    "REPLAY_RECORD_ID_KEY",
    "LedgerState",
    "LedgerEntry",
    "LedgerError",
    "ReplayLedger",
    "content_hash_of",
    "verify_record_id",
]


class LedgerError(DatasetError):
    """Raised when the ledger file itself is unusable. Never a silent fresh start on a
    populated ledger — that would re-ingest an already-committed history (§4.3)."""


#: Payload key under which the replay loop stamps a record's `record_id` onto the memory row.
#: `extra="allow"` (ADR-13.6) makes this migration-free, and it is what lets `rebuild_from_db`
#: reconstruct resume state exactly rather than heuristically matching on (type, event_time).
#:
#: **Contract for M4:** every record ingested by the replay loop MUST carry this key in its
#: payload, or a rebuilt ledger will under-report what is already committed — and under-reporting
#: means re-ingesting, which means duplicates.
REPLAY_RECORD_ID_KEY = "replay_record_id"


class LedgerState(str, Enum):
    """What the ledger knows about one record (§4.12's three-way decision)."""

    NEW = "new"  # never ingested — ingest it
    DONE = "done"  # ingested, content unchanged — skip it
    # ingested, but the content has since changed: report it, and act only under
    # --apply-corrections (§4.12 — acting automatically risks mass-supersession)
    CORRECTED = "corrected"


@dataclass(frozen=True)
class LedgerEntry:
    record_id: str
    content_hash: str | None  # None on a rebuilt entry — see ReplayLedger.rebuild_from_db
    memory_ids: list[str]
    ingested_at: str
    record: dict[str, Any] | None  # snapshot for offline diffs (§4.12); None when rebuilt
    rebuilt: bool = False

    def to_json(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "content_hash": self.content_hash,
            "memory_ids": self.memory_ids,
            "ingested_at": self.ingested_at,
            "record": self.record,
            "rebuilt": self.rebuilt,
        }

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> LedgerEntry:
        try:
            return cls(
                record_id=raw["record_id"],
                content_hash=raw.get("content_hash"),
                memory_ids=list(raw.get("memory_ids") or []),
                ingested_at=raw["ingested_at"],
                record=raw.get("record"),
                rebuilt=bool(raw.get("rebuilt", False)),
            )
        except KeyError as exc:
            raise LedgerError(f"ledger entry missing required field {exc}") from exc


# ── record_id verification ────────────────────────────────────────────────────────────────

_SLUG = r"[a-z][a-z0-9-]*"
_DAY = r"\d{4}-\d{2}-\d{2}"
#: `<rtype>.<date>` — one point event.
_POINT_ID_RE = re.compile(rf"^(?P<slug>{_SLUG})\.(?P<day>{_DAY})$")
#: `<rtype>.<start>.<end|ongoing>#<occurrence>` — one materialized day of a period.
_EXPANDED_ID_RE = re.compile(
    rf"^(?P<composition>{_SLUG}\.{_DAY}\.(?:{_DAY}|ongoing))#(?P<day>{_DAY})$"
)


def verify_record_id(record: ReplayRecord) -> None:
    """Reject any `record_id` the converter could not have produced (§4.3).

    `record_id` is converter-owned and read-only to humans: a hand-invented one breaks
    determinism the moment the dataset is regenerated, because the converter emits its own and
    the record then looks new — the P0 duplicate path. The rule is only useful if it is
    *checked*, so this is a guard rather than a README note (the `_GuardedSerde` precedent in
    graph-state-durability.md).

    **Scope, stated honestly.** For an expanded record the check is exact: the composition and
    occurrence date must reconstruct the id. For a point event the *type* half is not
    recoverable from the record alone — the record carries the engine type (`supplement`), which
    differs from the reconstruction type that formed the id (`medication`) wherever the type map
    folds them. So the grammar and the embedded date are verified and the slug is accepted.
    That still catches the threat this guard exists for — an invented or copy-pasted id — since
    both fail the grammar or the date check.
    """
    rid, day = record.record_id, record.event_time[:10]

    if record.expanded_from is not None:
        m = _EXPANDED_ID_RE.match(rid)
        if not m:
            raise LedgerError(
                f"record_id {rid!r} is not a converter-derived expanded id "
                f"(expected '<type>.<start>.<end|ongoing>#<date>')"
            )
        composition = record.expanded_from.get("composition")
        if m["composition"] != composition:
            raise LedgerError(
                f"record_id {rid!r} disagrees with its expanded_from.composition "
                f"({composition!r}) — ids are converter-owned and must not be hand-edited"
            )
        if m["day"] != day:
            raise LedgerError(
                f"record_id {rid!r} carries occurrence date {m['day']} but the record's "
                f"event_time is {day}"
            )
        return

    m = _POINT_ID_RE.match(rid)
    if not m:
        raise LedgerError(
            f"record_id {rid!r} is not a converter-derived point-event id "
            f"(expected '<type>.<date>'); a record with a '#' must carry expanded_from"
        )
    if m["day"] != day:
        raise LedgerError(
            f"record_id {rid!r} carries date {m['day']} but the record's event_time is {day}"
        )


def content_hash_of(record: ReplayRecord) -> str:
    """Hash the record's canonical form. Drift in *any* field — a corrected quantity, a changed
    date, a re-estimated macro — shows up here, which is what makes §4.12's correction path
    possible without re-reading the database."""
    return "sha256:" + hashlib.sha256(
        dumps_canonical(record.to_json()).encode("utf-8")
    ).hexdigest()


# ── the ledger ────────────────────────────────────────────────────────────────────────────


class ReplayLedger:
    """An append-only JSONL record of what replay has committed.

    Append-only is what makes the post-commit write ordering cheap to honor: one line, flushed
    immediately (§4.9), so a crash costs at most the in-flight record. It also makes a
    half-written trailing line — the one genuinely expected corruption — recoverable rather
    than fatal.
    """

    def __init__(self, path: Path, entries: dict[str, LedgerEntry] | None = None) -> None:
        self.path = path
        self._entries: dict[str, LedgerEntry] = entries or {}

    # ── loading ───────────────────────────────────────────────────────────────────────────
    @classmethod
    def load(cls, path: Path) -> ReplayLedger:
        """Read an existing ledger, or start an empty one if the file does not exist.

        Corruption handling distinguishes two cases, because conflating them is dangerous:

        * **A truncated final line** is the expected crash artifact — the process died mid-append.
          The line is discarded and the rest of the ledger stands.
        * **A malformed line anywhere else** is real corruption, and raises. Silently starting
          fresh here would re-ingest a partly-committed history and duplicate every record it
          had already written — the exact P0 outcome this class exists to prevent. The error
          points at `--rebuild-ledger`, which reconstructs state from the database instead.
        """
        if not path.exists():
            return cls(path)

        entries: dict[str, LedgerEntry] = {}
        lines = path.read_text(encoding="utf-8").splitlines()
        for lineno, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as exc:
                if lineno == len(lines):  # torn tail: the crash-during-append case
                    break
                raise LedgerError(
                    f"{path}:{lineno} is malformed — the ledger cannot be trusted, and starting "
                    f"fresh would re-ingest already-committed records. Rebuild it from the "
                    f"database with --rebuild-ledger. ({exc})"
                ) from exc
            entry = LedgerEntry.from_json(raw)
            entries[entry.record_id] = entry  # a later line supersedes an earlier one
        return cls(path, entries)

    # ── queries ───────────────────────────────────────────────────────────────────────────
    def entry(self, record_id: str) -> LedgerEntry | None:
        return self._entries.get(record_id)

    def is_done(self, record_id: str) -> bool:
        return record_id in self._entries

    def state(self, record: ReplayRecord) -> LedgerState:
        """The three-way decision M4's loop makes per record (§4.12).

        **This is where the record_id guard fires**, because it is the one method every record
        passes through on every run — including the ones that will be skipped. Verifying only at
        ingest would miss the more dangerous edit: changing an *already-committed* record's id
        makes it look NEW, and the run would happily re-ingest it as a duplicate. Same posture as
        `_GuardedSerde` (graph-state-durability.md) — put the check on the path nothing can
        bypass, rather than trusting each caller to remember it.

        A **rebuilt** entry reports DONE even though its content cannot be compared: rebuilding
        recovers *what* was committed, not the bytes it was committed with. Failing to DONE is
        the safe direction — treating unknown content as CORRECTED would mass-supersede the
        whole dataset on the first run after a rebuild.
        """
        verify_record_id(record)
        entry = self._entries.get(record.record_id)
        if entry is None:
            return LedgerState.NEW
        if entry.content_hash is None:  # rebuilt: no basis for comparison
            return LedgerState.DONE
        return (
            LedgerState.DONE
            if entry.content_hash == content_hash_of(record)
            else LedgerState.CORRECTED
        )

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, record_id: object) -> bool:
        return record_id in self._entries

    # ── writing ───────────────────────────────────────────────────────────────────────────
    def mark_done(
        self,
        record: ReplayRecord,
        memory_ids: list[UUID] | list[str],
        *,
        now: datetime | None = None,
    ) -> LedgerEntry:
        """Record that ``record`` committed as ``memory_ids``.

        **Call this only after the ingest call has returned.** The ordering is the invariant
        (see the module docstring); calling it earlier would mark work that may never commit.

        The full record is snapshotted so §4.12 can render a field-level diff with no database
        read and no model call when the record later changes.
        """
        verify_record_id(record)  # belt-and-braces: state() is the primary gate, not the only one
        entry = LedgerEntry(
            record_id=record.record_id,
            content_hash=content_hash_of(record),
            memory_ids=[str(m) for m in memory_ids],
            ingested_at=(now or datetime.now(timezone.utc))
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z"),
            record=record.to_json(),
        )
        self._append(entry)
        return entry

    def _append(self, entry: LedgerEntry) -> None:
        """Append one line and flush (§4.9 — per record, not per batch, so a crash costs at
        most the in-flight record)."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(dumps_canonical(entry.to_json()) + "\n")
            fh.flush()
        self._entries[entry.record_id] = entry

    # ── recovery ──────────────────────────────────────────────────────────────────────────
    @classmethod
    def rebuild_from_db(
        cls, path: Path, cur: psycopg.Cursor, user_id: UUID, *, now: datetime | None = None
    ) -> ReplayLedger:
        """Reconstruct resume state from committed rows, replacing the ledger file (§4.3).

        A bare append-only file is a single point of failure; losing it must not be fatal, or
        the only remaining options are re-ingesting (duplicates) or reconciling by hand.

        **What rebuilding recovers, and what it cannot.** It recovers exactly which records are
        committed and which rows they produced, by reading the `replay_record_id` each row
        carries in its payload. It cannot recover the *content* those records had when they were
        ingested — that lived only in the lost file — so entries come back with no
        `content_hash` and drift detection is unavailable for them until they are ingested
        again. `state()` reports them DONE, failing safe.
        """
        cur.execute(
            f"""
            SELECT payload ->> '{REPLAY_RECORD_ID_KEY}' AS record_id,
                   id,
                   created_at
            FROM memories
            WHERE user_id = %s
              AND source = 'replay'
              AND status = 'active'
              AND payload ->> '{REPLAY_RECORD_ID_KEY}' IS NOT NULL
            ORDER BY created_at, id
            """,
            [user_id],
        )
        grouped: dict[str, list[str]] = {}
        for row in cur.fetchall():
            grouped.setdefault(row["record_id"], []).append(str(row["id"]))

        stamp = (
            (now or datetime.now(timezone.utc))
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )
        ledger = cls(path, {})
        # Rewrite rather than append: the file is being replaced by a database-derived truth,
        # and mixing it with whatever survived would produce a ledger that is neither.
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8", newline="\n")
        for record_id in sorted(grouped):
            ledger._append(
                LedgerEntry(
                    record_id=record_id,
                    content_hash=None,
                    memory_ids=grouped[record_id],
                    ingested_at=stamp,
                    record=None,
                    rebuilt=True,
                )
            )
        return ledger
