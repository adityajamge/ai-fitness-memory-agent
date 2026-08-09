"""Manual nutrition backfill — the sweep for meals logged before the nutrition stage existed,
or logged while the estimate call was failing.

The counterpart to ``cli/backfill.py`` (embeddings), and for the same reason: nutrition is a
nullable enrichment, so a meal can commit without it and be filled in later rather than
failing the turn. No scheduler (event-driven engine, ADR-3) — ingestion fills new meals inline
and this is the catch-up.

**Idempotent, and it never overwrites.** Only meals with no ``nutrition`` key at all are
candidates, and the UPDATE re-asserts that condition, so a value the user stated, a reviewed
replay-table macro set, or an estimate a previous run already wrote is never touched. Running
it twice does the work once.

Usage:
    python -m cli.backfill_nutrition --user <uuid>     # one user
    python -m cli.backfill_nutrition --all             # every user with meals missing nutrition
    python -m cli.backfill_nutrition --all --dry-run   # count candidates, call no model
"""

from __future__ import annotations

import argparse
from uuid import UUID

from agent.providers import build_default_provider
from engine.config import load_settings
from engine.db import Database
from engine.ingestion import IngestionService

_CANDIDATES = """
SELECT user_id, COUNT(*) AS n
FROM memories
WHERE type = 'meal' AND status = 'active'
      AND NOT (payload ? 'nutrition')
      AND jsonb_array_length(COALESCE(payload -> 'items', '[]'::JSONB)) > 0
GROUP BY user_id
ORDER BY user_id
"""


def _users_with_gaps(db: Database) -> list[tuple[UUID, int]]:
    with db.transaction() as cur:
        cur.execute(_CANDIDATES)
        return [(row["user_id"], row["n"]) for row in cur.fetchall()]


def _backfill_user(svc: IngestionService, user_id: UUID) -> int:
    """Drain a user's un-estimated meals in bounded pages until none remain.

    Terminates because a page that fills nothing returns 0: rows the model cannot estimate stay
    un-estimated and are simply re-offered on the next explicit run, rather than spinning here.
    """
    total = 0
    while True:
        filled = svc.backfill_nutrition(user_id, svc.backfill_batch)
        total += filled
        if filled == 0:
            break
    return total


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill missing meal nutrition estimates.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--user", type=UUID, help="backfill a single user by UUID")
    group.add_argument("--all", action="store_true", help="backfill every user with gaps")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report how many meals would be estimated, without calling the model",
    )
    args = parser.parse_args()

    settings = load_settings()
    db = Database(settings.database_url)

    gaps = _users_with_gaps(db)
    if args.user:
        gaps = [(u, n) for u, n in gaps if u == args.user]
    if not gaps:
        print("no meals need nutrition backfilling")
        return

    if args.dry_run:
        for user_id, n in gaps:
            print(f"user {user_id}: {n} meal(s) would be estimated")
        print(f"total: {sum(n for _, n in gaps)} meal(s)")
        return

    svc = IngestionService(
        db,
        build_default_provider(settings),
        default_tz=settings.default_tz,
        backfill_batch=settings.backfill_batch,
    )
    for user_id, _ in gaps:
        count = _backfill_user(svc, user_id)
        print(f"user {user_id}: estimated {count} meal(s)")


if __name__ == "__main__":
    main()
