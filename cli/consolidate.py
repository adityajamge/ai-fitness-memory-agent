"""Retroactive consolidation sweep (Phase 5 §4.11, M5d).

**Why this command has to exist.** Consolidation is event-driven (ADR-3): it fires when an
ingest touches a series, or when the agent calls ``analyze_series``. The
builder's account was populated by the Phase 4 replay, which finished on 2026-08-02 and is
idempotent — re-running it processes nothing. So without this sweep the mature account ships
with **zero insights**, and five things break at once: the money shot's "had already flagged
it" clause, the top-bar insight count, the insights pane, the lineage graph, and the timeline's
change markers.

**It reuses ``ConsolidationService`` and adds no logic of its own.** Not for tidiness: a second
copy of the identity rule would be a second place duplicate insights can be born, and I-12's
whole point is that exactly one such place exists. This module reads settings, builds the same
service the app builds, picks users, and prints — the same shape ``cli/backfill.py`` uses for
the same reason.

**Honesty (I-18, [ADR-13.10](../docs/office-hours/09-decisions.md#adr-13)).** Insights derived
here carry a truthful ``created_at`` of *now*, even though the evidence under them is months
old. They are framed in **event time** — "this pattern emerged in your May–June data" — and the
"flagged the moment it happened" language belongs exclusively to the live path, where it is
provably true. There is deliberately no back-dating flag: a replay clock in the write path was
rejected, and adding one here through the back door would be the same decision unmade.

Usage::

    python -m cli.consolidate --user <uuid>        # one account
    python -m cli.consolidate --all                # every account with memories
    python -m cli.consolidate --all --dry-run      # report only; writes nothing
"""

from __future__ import annotations

import argparse
import logging
from uuid import UUID

from engine.config import load_settings
from engine.consolidation import ConsolidationOutcome, ConsolidationService
from engine.db import Database

logger = logging.getLogger(__name__)

#: The per-series budget (§4.8) exists to protect an interactive turn. A sweep is an operator
#: command with nobody waiting on a request, so deferring series here would just mean an
#: incomplete pass that has to be run again. Generous rather than unbounded: a genuinely stuck
#: series still ends rather than hanging a run forever.
SWEEP_BUDGET_MS = 10 * 60 * 1000


def users_with_memories(db: Database) -> list[UUID]:
    """Every account holding at least one active memory, oldest first.

    Insights are excluded from the *trigger* — an account whose only rows are insights has
    nothing new to consolidate, and including them would make ``--all`` grow with its own
    output.
    """
    with db.transaction() as cur:
        cur.execute(
            """
            SELECT user_id, min(created_at) AS first_seen
            FROM memories
            WHERE status = 'active' AND type <> 'insight'
            GROUP BY user_id
            ORDER BY first_seen
            """
        )
        return [row["user_id"] for row in cur.fetchall()]


def consolidate_user(
    service: ConsolidationService, user_id: UUID, *, dry_run: bool = False
) -> ConsolidationOutcome:
    """Sweep every consolidatable series for one account.

    ``dry_run`` routes through ``analyze`` instead of ``consolidate``: it runs the identical
    read-collapse-detect path and reports what *would* happen, without the identity comparison
    ever reaching a write. That is what makes the flag trustworthy — it is not a different
    algorithm with a printing branch, it is the same one stopped one step earlier.
    """
    if dry_run:
        return service.analyze(user_id, budget_ms=SWEEP_BUDGET_MS)
    return service.consolidate(user_id, budget_ms=SWEEP_BUDGET_MS)


def _describe(outcome: ConsolidationOutcome, *, dry_run: bool) -> str:
    created = len(outcome.created_ids)
    superseded = len(outcome.superseded_ids)
    unchanged = sum(1 for o in outcome.outcomes if o.unchanged)
    refused = sum(1 for o in outcome.outcomes if o.refused)
    if dry_run:
        verb, count = "would derive", outcome.would_create_count
    else:
        verb, count = "derived", created
    parts = [f"{verb} {count}", f"unchanged {unchanged}", f"no pattern {refused}"]
    if superseded:
        parts.insert(1, f"superseded {superseded}")
    if outcome.deferred:
        parts.append(f"deferred {len(outcome.deferred)}")
    return ", ".join(parts)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Derive insights over history already in the database (Phase 5 §4.11).",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--user", type=UUID, help="consolidate a single account by UUID")
    group.add_argument("--all", action="store_true", help="consolidate every account")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would be derived; write nothing",
    )
    args = parser.parse_args(argv)

    settings = load_settings()
    db = Database(settings.database_url)
    # The same service the app builds (api/main.py), constructed the same way. No model
    # provider is needed at all: consolidation makes no model call, and insights are written
    # unembedded for the existing backfill to pick up (I-16).
    service = ConsolidationService(db, default_tz=settings.default_tz)

    user_ids = [args.user] if args.user else users_with_memories(db)
    if not user_ids:
        print("no accounts with memories to consolidate")
        return 0

    if args.dry_run:
        print("dry run — nothing will be written")

    total_created = 0
    failed = 0
    for user_id in user_ids:
        try:
            outcome = consolidate_user(service, user_id, dry_run=args.dry_run)
        except Exception as exc:  # noqa: BLE001 — one bad account must not end the sweep
            logger.exception("consolidation failed for user %s", user_id)
            print(f"user {user_id}: FAILED — {exc}")
            failed += 1
            continue
        total_created += (
            outcome.would_create_count if args.dry_run else len(outcome.created_ids)
        )
        print(f"user {user_id}: {_describe(outcome, dry_run=args.dry_run)}")

    scope = "would be derived" if args.dry_run else "derived"
    print(f"{len(user_ids)} account(s); {total_created} insight(s) {scope}")
    if failed:
        print(f"{failed} account(s) failed — see the log above")
    return 1 if failed else 0


if __name__ == "__main__":  # pragma: no cover — exercised via main()
    raise SystemExit(main())
