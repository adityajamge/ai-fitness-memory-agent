"""The replay CLI (T8 M4) — pushes a JSONL dataset through the production ingestion pipeline.

Design: docs/engineering/replay-architecture.md §3 (per-record contract), §4.3 (idempotent
resume), §4.10 (halt threshold + failure artifact), §4.12 (correction workflow), §4.14
(correction-workflow transaction), §4.15 (halt-counter/exit-code/freshness-check rules).
Composition-root pattern mirrors cli/backfill.py: load_settings() -> Database ->
IngestionService, argparse, main().

Orchestration only: every write goes through IngestionService's ``ingest_events`` (new
records) or ``ingest_events_superseding`` (corrections) — this module never opens a
transaction or calls engine.repository directly.

Not yet implemented: --rebuild-ledger CLI wiring (ReplayLedger.rebuild_from_db already exists
and is tested at the M2 level; only the flag parsing + call-through is outstanding).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from agent.providers import build_default_provider
from cli.replay_dataset import DatasetError, Manifest, ReplayRecord, load_manifest, read_dataset
from cli.replay_ledger import (
    REPLAY_RECORD_ID_KEY,
    LedgerEntry,
    LedgerError,
    LedgerState,
    ReplayLedger,
)
from engine.config import load_settings
from engine.db import Database
from engine.ingestion import IngestionService
from engine.types import UnknownMemoryType, ValidationError

logger = logging.getLogger(__name__)

HALT_THRESHOLD = 5  # consecutive record-level failures before a halt (§4.10)

EXIT_OK = 0
EXIT_FAILURES = 1
EXIT_HALTED = 2
EXIT_FATAL = 3


@dataclass
class ReplaySummary:
    new: int = 0
    skipped: int = 0
    corrected_reported: int = 0
    corrected_applied: int = 0
    failed: int = 0
    halted: bool = False


@dataclass(frozen=True)
class _FailureRecord:
    """One row of the run's failure artifact (§4.10's field table)."""

    record_id: str
    record_number: int
    jsonl_line: int
    source_record: dict
    constructed_payload: dict
    validation_errors: list
    source_ref: str

    def to_json(self) -> dict:
        return {
            "record_id": self.record_id,
            "record_number": self.record_number,
            "jsonl_line": self.jsonl_line,
            "source_record": self.source_record,
            "constructed_payload": self.constructed_payload,
            "validation_errors": self.validation_errors,
            "source_ref": self.source_ref,
        }


def _errors_for(exc: Exception) -> list:
    """Normalize any caught exception into the artifact's validation_errors shape.

    A real ``ValidationError`` gets pydantic's structured ``.errors()``; ``UnknownMemoryType``
    and ``LedgerError`` (a bad/tampered record_id) are not pydantic errors, so they get the
    same one-item shape instead of a second field name — one artifact schema for every
    record-level failure class §4.10 halts on.
    """
    if isinstance(exc, ValidationError):
        return exc.errors()
    return [{"msg": str(exc), "type": type(exc).__name__}]


def _write_failure(path: Path, failure: _FailureRecord) -> None:
    """Append one failure to the run artifact, flushed immediately — same append-and-flush
    posture as ReplayLedger._append, so a halt (or a crash) never loses an already-recorded
    failure."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(failure.to_json(), sort_keys=True, ensure_ascii=False, default=str))
        fh.write("\n")
        fh.flush()


def _flatten(obj: object, prefix: str = "") -> dict[str, object]:
    if isinstance(obj, dict):
        out: dict[str, object] = {}
        for key, value in obj.items():
            out.update(_flatten(value, f"{prefix}.{key}" if prefix else str(key)))
        return out
    if isinstance(obj, list):
        out = {}
        for i, value in enumerate(obj):
            out.update(_flatten(value, f"{prefix}[{i}]"))
        return out
    return {prefix: obj}


def _print_diff(record: ReplayRecord, entry: LedgerEntry) -> None:
    """The §4.12 review gate: print unconditionally, flag or not, so nothing is superseded
    that hasn't already been seen. ``entry.record`` is guaranteed non-None here — a rebuilt
    entry (record=None) can never reach CORRECTED, since ledger.state() reports it DONE."""
    old_flat = _flatten(entry.record or {})
    new_flat = _flatten(record.to_json())
    day = record.event_time[:10]
    print(f"CHANGED  {record.type} / {day}  (record_id: {record.record_id})")
    for key in sorted(set(old_flat) | set(new_flat)):
        old_val, new_val = old_flat.get(key), new_flat.get(key)
        if old_val != new_val:
            print(f"  {key:30s} {old_val!r} -> {new_val!r}")
    print()


def run_replay(
    svc: IngestionService,
    ledger: ReplayLedger,
    records: list[ReplayRecord],
    user_id: UUID,
    *,
    failure_log_path: Path,
    apply_corrections: bool = False,
    force: bool = False,
) -> ReplaySummary:
    """Iterate ``records`` in file order: ingest NEW, skip DONE, report (and, under
    ``apply_corrections``, supersede) CORRECTED. Halts after ``HALT_THRESHOLD`` consecutive
    record-level failures unless ``force`` is set (§4.10); any non-failure outcome resets the
    counter (§4.15).

    The ledger write happens **strictly after** the ingest call returns (§4.3) for both NEW
    and applied-CORRECTED records — never before, never batched.
    """
    summary = ReplaySummary()
    total = len(records)
    consecutive_failures = 0

    for i, record in enumerate(records, start=1):
        constructed_payload: dict = record.payload
        try:
            state = ledger.state(record)  # may raise LedgerError -- a bad/tampered record_id

            if state is LedgerState.DONE:
                summary.skipped += 1
                consecutive_failures = 0
                logger.info("[%d/%d] SKIP %s", i, total, record.record_id)
                continue

            if state is LedgerState.NEW:
                event = record.as_extracted_event(
                    extra_payload={REPLAY_RECORD_ID_KEY: record.record_id}
                )
                constructed_payload = event.payload
                receipt = svc.ingest_events(user_id, [event])
                ledger.mark_done(record, [ref.id for ref in receipt.created])
                summary.new += 1
                consecutive_failures = 0
                logger.info("[%d/%d] NEW %s", i, total, record.record_id)
                continue

            # CORRECTED
            entry = ledger.entry(record.record_id)
            assert entry is not None  # CORRECTED implies a prior entry exists (ledger.state)
            _print_diff(record, entry)

            if not apply_corrections:
                summary.corrected_reported += 1
                consecutive_failures = 0
                logger.info("[%d/%d] CORRECTED (reported) %s", i, total, record.record_id)
                continue

            event = record.as_extracted_event(
                extra_payload={REPLAY_RECORD_ID_KEY: record.record_id}
            )
            constructed_payload = event.payload
            superseded_ids = [UUID(mid) for mid in entry.memory_ids]
            receipt = svc.ingest_events_superseding(user_id, [event], superseded_ids)
            ledger.mark_done(record, [ref.id for ref in receipt.created])
            summary.corrected_applied += 1
            consecutive_failures = 0
            logger.info("[%d/%d] CORRECTED (applied) %s", i, total, record.record_id)

        except (ValidationError, UnknownMemoryType, LedgerError) as exc:
            summary.failed += 1
            consecutive_failures += 1
            _write_failure(
                failure_log_path,
                _FailureRecord(
                    record_id=record.record_id,
                    record_number=i,
                    jsonl_line=i,  # homogeneous JSONL -- always equal (§4.1)
                    source_record=record.to_json(),
                    constructed_payload=constructed_payload,
                    validation_errors=_errors_for(exc),
                    source_ref=record.source_ref,
                ),
            )
            logger.warning("[%d/%d] FAILED %s (%s)", i, total, record.record_id, exc)

            if consecutive_failures >= HALT_THRESHOLD and not force:
                summary.halted = True
                logger.error(
                    "halting after %d consecutive failures; see %s",
                    consecutive_failures,
                    failure_log_path,
                )
                return summary

    return summary


def _check_freshness(manifest: Manifest) -> list[str]:
    """Advisory only, never blocking (§4.15). Silent no-op when a local source file is
    absent — both are gitignored (ADR-7) and a shipped JSONL may travel without them."""
    warnings: list[str] = []
    sources = (
        ("source markdown", manifest.source_document, manifest.source_document_sha256),
        ("payload table", manifest.payload_table, manifest.payload_table_sha256),
    )
    for label, rel_path, expected_hash in sources:
        path = Path(rel_path)
        try:
            if not path.exists():
                continue
            actual_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            continue  # advisory -- a probe failure must never block the run
        if actual_hash != expected_hash:
            warnings.append(
                f"warning: {label} ({rel_path}) has changed since this dataset was "
                f"generated -- consider regenerating with cli.convert"
            )
    return warnings


def _default_ledger_path(dataset_path: Path) -> Path:
    return dataset_path.parent / f"{dataset_path.stem}.ledger.jsonl"


def _default_failure_log_path(ledger_path: Path, *, now: datetime | None = None) -> Path:
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    return ledger_path.parent / f"replay-failures-{stamp}.jsonl"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Replay a JSONL dataset into a user's account (T8 M4)."
    )
    parser.add_argument("--dataset", type=Path, required=True, help="path to the JSONL dataset")
    parser.add_argument("--user", type=UUID, required=True, help="target account UUID")
    parser.add_argument(
        "--ledger", type=Path, help="ledger file path (default: <dataset-stem>.ledger.jsonl)"
    )
    parser.add_argument(
        "--apply-corrections",
        action="store_true",
        help="supersede CORRECTED records instead of only reporting them (§4.12)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help=f"continue past the {HALT_THRESHOLD}-consecutive-failure halt (§4.10)",
    )
    args = parser.parse_args()

    ledger_path = args.ledger or _default_ledger_path(args.dataset)

    try:
        manifest = load_manifest(args.dataset)
        records = read_dataset(args.dataset)
        ledger = ReplayLedger.load(ledger_path)
    except DatasetError as exc:
        print(f"fatal: {exc}")
        sys.exit(EXIT_FATAL)

    for warning in _check_freshness(manifest):
        print(warning)

    settings = load_settings()
    db = Database(settings.database_url)
    svc = IngestionService(
        db,
        build_default_provider(settings),
        default_tz=settings.default_tz,
        backfill_batch=settings.backfill_batch,
    )

    failure_log_path = _default_failure_log_path(ledger_path)
    summary = run_replay(
        svc,
        ledger,
        records,
        args.user,
        failure_log_path=failure_log_path,
        apply_corrections=args.apply_corrections,
        force=args.force,
    )

    print(
        f"{summary.new} new, {summary.skipped} skipped, "
        f"{summary.corrected_reported} corrected (reported), "
        f"{summary.corrected_applied} corrected (applied), "
        f"{summary.failed} failed"
    )

    if summary.halted:
        print(f"HALTED after {HALT_THRESHOLD} consecutive failures -- see {failure_log_path}")
        sys.exit(EXIT_HALTED)
    if summary.failed:
        print(f"see {failure_log_path} for failure details")
        sys.exit(EXIT_FAILURES)
    sys.exit(EXIT_OK)


if __name__ == "__main__":
    main()
