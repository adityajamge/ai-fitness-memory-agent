"""Manual embedding backfill (T15).

Backfill has no scheduler home by design (event-driven engine, ADR-3): embeddings are
filled opportunistically on each ingest turn, and this CLI is the manual sweep for rows that
are still NULL (e.g. after a Bedrock outage, or a bulk replay import). Idempotent — rows that
already have an embedding are never revisited.

Usage:
    python -m cli.backfill --user <uuid>     # one user
    python -m cli.backfill --all             # every user with NULL-embedding rows
"""

from __future__ import annotations

import argparse
from uuid import UUID

from agent.providers import build_default_provider
from engine.config import load_settings
from engine.db import Database
from engine.ingestion import IngestionService


def _users_with_gaps(db: Database) -> list[UUID]:
    with db.transaction() as cur:
        cur.execute(
            "SELECT DISTINCT user_id FROM memories WHERE embedding IS NULL AND status = 'active'"
        )
        return [row["user_id"] for row in cur.fetchall()]


def _backfill_user(svc: IngestionService, user_id: UUID) -> int:
    """Drain a user's NULL-embedding rows in bounded pages until none remain."""
    total = 0
    while True:
        embedded = svc.backfill_embeddings(user_id, svc.backfill_batch)
        total += embedded
        if embedded == 0:
            break
    return total


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill NULL memory embeddings (T15).")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--user", type=UUID, help="backfill a single user by UUID")
    group.add_argument("--all", action="store_true", help="backfill every user with gaps")
    args = parser.parse_args()

    settings = load_settings()
    db = Database(settings.database_url)
    svc = IngestionService(
        db,
        build_default_provider(settings),
        default_tz=settings.default_tz,
        backfill_batch=settings.backfill_batch,
    )

    user_ids = [args.user] if args.user else _users_with_gaps(db)
    if not user_ids:
        print("no rows need backfilling")
        return
    for user_id in user_ids:
        count = _backfill_user(svc, user_id)
        print(f"user {user_id}: embedded {count} row(s)")


if __name__ == "__main__":
    main()
