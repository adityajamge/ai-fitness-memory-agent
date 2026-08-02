"""The replay CLI (T8 M4) — pushes a JSONL dataset through the production ingestion pipeline.

Design: docs/engineering/replay-architecture.md §3 (per-record contract), §4.3 (idempotent
resume), §4.14 (correction-workflow transaction). Composition-root pattern mirrors
cli/backfill.py: load_settings() -> Database -> IngestionService, argparse, main().

**This commit implements the NEW/DONE path only** — the minimal loop that proves the
ledger/ingestion wiring is correct before anything else layers on top (implementation-order
rationale in the M4 spec: prove resume first, since a bug here is the P0 class). Not yet
implemented, landing in later commits: CORRECTED-record handling (§4.12), the §4.10 halt
threshold and failure artifact, --apply-corrections, --force, --rebuild-ledger, and the
advisory freshness check (§4.15). A CORRECTED record is therefore a hard error here, not a
silent skip — see run_replay.
"""

from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from agent.providers import build_default_provider
from cli.replay_dataset import ReplayRecord, read_dataset
from cli.replay_ledger import REPLAY_RECORD_ID_KEY, LedgerState, ReplayLedger
from engine.config import load_settings
from engine.db import Database
from engine.ingestion import IngestionService

logger = logging.getLogger(__name__)


@dataclass
class ReplaySummary:
    new: int = 0
    skipped: int = 0
    corrected_reported: int = 0
    corrected_applied: int = 0
    failed: int = 0


def run_replay(
    svc: IngestionService, ledger: ReplayLedger, records: list[ReplayRecord], user_id: UUID
) -> ReplaySummary:
    """Iterate ``records`` in file order, ingesting NEW ones and skipping DONE ones.

    The ledger write happens **strictly after** the ingest call returns (§4.3's load-bearing
    invariant) — never batched, never speculative. A CORRECTED record raises
    ``NotImplementedError``: this commit does not yet know how to act on one, and a silent
    skip here would be exactly the kind of "run reports success, database still wrong"
    outcome §4.12 exists to prevent.
    """
    summary = ReplaySummary()
    total = len(records)
    for i, record in enumerate(records, start=1):
        state = ledger.state(record)

        if state is LedgerState.DONE:
            summary.skipped += 1
            logger.info("[%d/%d] SKIP %s", i, total, record.record_id)
            continue

        if state is LedgerState.NEW:
            event = record.as_extracted_event(
                extra_payload={REPLAY_RECORD_ID_KEY: record.record_id}
            )
            receipt = svc.ingest_events(user_id, [event])
            ledger.mark_done(record, [ref.id for ref in receipt.created])
            summary.new += 1
            logger.info("[%d/%d] NEW %s", i, total, record.record_id)
            continue

        raise NotImplementedError(
            f"record {record.record_id!r} is CORRECTED; correction handling is not yet "
            f"implemented (§4.12 — lands in a later commit)"
        )
    return summary


def _default_ledger_path(dataset_path: Path) -> Path:
    return dataset_path.parent / f"{dataset_path.stem}.ledger.jsonl"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Replay a JSONL dataset into a user's account (T8 M4)."
    )
    parser.add_argument("--dataset", type=Path, required=True, help="path to the JSONL dataset")
    parser.add_argument("--user", type=UUID, required=True, help="target account UUID")
    parser.add_argument(
        "--ledger", type=Path, help="ledger file path (default: <dataset-stem>.ledger.jsonl)"
    )
    args = parser.parse_args()

    ledger_path = args.ledger or _default_ledger_path(args.dataset)

    settings = load_settings()
    db = Database(settings.database_url)
    svc = IngestionService(
        db,
        build_default_provider(settings),
        default_tz=settings.default_tz,
        backfill_batch=settings.backfill_batch,
    )

    records = read_dataset(args.dataset)
    ledger = ReplayLedger.load(ledger_path)

    summary = run_replay(svc, ledger, records, args.user)
    print(f"{summary.new} new, {summary.skipped} skipped")


if __name__ == "__main__":
    main()
