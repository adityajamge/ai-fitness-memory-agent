"""The retroactive consolidation sweep (Phase 5 M5d, §4.11).

This command exists because consolidation is event-driven and the builder's account was filled
by a replay that is idempotent — so nothing would ever trigger a derivation over it, and the
mature account would ship with zero insights.

What matters here is not that it works once, but that it is **safe to run repeatedly against
the account the demo depends on**. So the load-bearing assertions are:

* **I-12 through the CLI** — a second identical run writes zero rows.
* **`--dry-run` writes nothing**, and reports the same verdict the real run then produces. A dry
  run that could disagree with the real run would be worse than none, because an operator would
  trust it.
* **One implementation** — the CLI holds no consolidation logic; it composes the same
  ``ConsolidationService`` the app builds.
* **I-18** — insights derived retroactively carry a truthful ``created_at`` of *now*.
"""

from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest

import cli.consolidate as consolidate_cli
from engine.config import Settings
from engine.consolidation import ConsolidationService
from engine.ingestion import IngestionService
from engine.model import ExtractedEvent
from engine.tests.conftest import DATABASE_URL, FakeModelProvider
from engine.tests.dbcleanup import new_user

TZ = "Asia/Kolkata"
IST = timezone(timedelta(hours=5, minutes=30))


def _at(day: str) -> datetime:
    return datetime.fromisoformat(f"{day}T12:00:00").replace(tzinfo=IST)


def _meal(day: str, protein: float, composition: str) -> ExtractedEvent:
    return ExtractedEvent(
        type="meal", event_time=_at(day), tz=TZ, confidence=0.6, summary=f"meal {protein:g}g",
        payload={
            "nutrition": {"protein_g": protein}, "items": [],
            "expanded_from": {"composition": composition, "assertion": f"{protein:g} g/day"},
        },
    )


def _phase(start: str, days: int, protein: float, composition: str) -> list[ExtractedEvent]:
    first = datetime.fromisoformat(start).date()
    return [
        _meal((first + timedelta(days=n)).isoformat(), protein, composition)
        for n in range(days)
    ]


@pytest.fixture()
def replayed(db, user_id):
    """History in the database with **no insights over it** — the exact state the account is in
    after the Phase 4 replay, which is what this command exists to fix."""
    quiet = IngestionService(db, FakeModelProvider([]), default_tz=TZ)  # no consolidation
    quiet.ingest_events(user_id, _phase("2026-05-01", 8, 30.0, "phase-a"))
    quiet.ingest_events(user_id, _phase("2026-05-09", 8, 60.0, "phase-b"))
    return user_id


@pytest.fixture()
def cli(monkeypatch):
    """Run the CLI against the test database, patching only the composition seam it exposes."""
    monkeypatch.setattr(
        consolidate_cli, "load_settings",
        lambda: Settings(database_url=DATABASE_URL, default_tz=TZ),
    )
    return consolidate_cli


def _insights(db, user_id: UUID, status: str = "active") -> list[dict]:
    with db.transaction() as cur:
        cur.execute(
            "SELECT id, created_at, event_time, status, confidence, provenance, "
            "(embedding IS NOT NULL) AS embedded, payload "
            "FROM memories WHERE user_id = %s AND type = 'insight' AND status = %s",
            [user_id, status],
        )
        return cur.fetchall()


def _rows(db, user_id: UUID) -> int:
    with db.transaction() as cur:
        cur.execute("SELECT count(*) AS n FROM memories WHERE user_id = %s", [user_id])
        return int(cur.fetchone()["n"])


# ══ the reason the command exists ══════════════════════════════════════════════════════
def test_it_derives_insights_over_history_nothing_else_would_touch(db, replayed, cli, capsys):
    """Replay is idempotent, so re-running it processes nothing; without this sweep the mature
    account would hold zero insights."""
    assert _insights(db, replayed) == []

    assert cli.main(["--user", str(replayed)]) == 0

    assert len(_insights(db, replayed)) == 1
    assert "derived 1" in capsys.readouterr().out


# ══ I-12 — idempotence, the property this command is judged on ═════════════════════════
def test_a_second_identical_run_writes_zero_rows(db, replayed, cli, capsys):
    """**I-12 through the CLI.** This is what makes the command safe to re-run against the one
    account the demo depends on."""
    cli.main(["--user", str(replayed)])
    after_first = _rows(db, replayed)
    capsys.readouterr()

    assert cli.main(["--user", str(replayed)]) == 0

    assert _rows(db, replayed) == after_first
    assert len(_insights(db, replayed)) == 1
    assert _insights(db, replayed, status="superseded") == []  # no supersession churn either
    assert "derived 0" in capsys.readouterr().out


def test_a_third_run_is_still_a_no_op(db, replayed, cli):
    for _ in range(3):
        cli.main(["--user", str(replayed)])
    assert len(_insights(db, replayed)) == 1


def test_new_data_that_changes_the_claim_supersedes(db, replayed, cli):
    """Idempotent is not inert: when the data genuinely moves, the sweep records it."""
    cli.main(["--user", str(replayed)])
    original = _insights(db, replayed)[0]["id"]

    quiet = IngestionService(db, FakeModelProvider([]), default_tz=TZ)
    quiet.ingest_events(replayed, _phase("2026-05-17", 8, 120.0, "phase-c"))
    cli.main(["--user", str(replayed)])

    active = _insights(db, replayed)
    assert len(active) == 1 and active[0]["id"] != original
    assert [r["id"] for r in _insights(db, replayed, status="superseded")] == [original]


# ══ --dry-run ══════════════════════════════════════════════════════════════════════════
def test_dry_run_writes_nothing(db, replayed, cli, capsys):
    before = _rows(db, replayed)

    assert cli.main(["--user", str(replayed), "--dry-run"]) == 0

    assert _rows(db, replayed) == before
    assert _insights(db, replayed) == []
    out = capsys.readouterr().out
    assert "dry run" in out and "would derive 1" in out


def test_dry_run_predicts_what_the_real_run_then_does(db, replayed, cli, capsys):
    """The property that makes the flag trustworthy: it is the same path stopped one step
    earlier, not a different algorithm with a printing branch."""
    cli.main(["--user", str(replayed), "--dry-run"])
    predicted = capsys.readouterr().out

    cli.main(["--user", str(replayed)])
    actual = capsys.readouterr().out

    assert "would derive 1" in predicted
    assert "derived 1" in actual
    assert len(_insights(db, replayed)) == 1


def test_dry_run_after_a_real_run_reports_nothing_left_to_do(db, replayed, cli, capsys):
    cli.main(["--user", str(replayed)])
    capsys.readouterr()

    cli.main(["--user", str(replayed), "--dry-run"])

    assert "would derive 0" in capsys.readouterr().out


def test_dry_run_still_validates_what_it_would_write(db, replayed):
    """A dry run that skipped payload validation could promise a claim the real run then
    rejects — the one way this flag could mislead."""
    service = ConsolidationService(db, default_tz=TZ, budget_ms=60_000)
    outcome = service.analyze(replayed)
    assert outcome.would_create_count == 1
    assert outcome.created_ids == []


# ══ --user / --all ═════════════════════════════════════════════════════════════════════
def test_all_sweeps_every_discovered_account(db, replayed, cli, capsys, monkeypatch):
    """``--all`` iterates whatever discovery returns, consolidating each and aggregating one
    report.

    **Discovery is stubbed deliberately.** Letting the real ``--all`` run here would sweep every
    account in the shared test cluster — during a full-suite run that is ~150 accumulated users
    x 9 series, roughly fourteen minutes of database work inside one test, and the contention
    that comes with it. That is precisely the unbounded-sweep pattern TODOS.md already records
    against ``test_backfill.py``. The sweep *loop* is what this test owns; discovery has its own
    test below."""
    other = new_user()
    quiet = IngestionService(db, FakeModelProvider([]), default_tz=TZ)
    quiet.ingest_events(other, _phase("2026-05-01", 8, 40.0, "x-a"))
    quiet.ingest_events(other, _phase("2026-05-09", 8, 90.0, "x-b"))
    monkeypatch.setattr(cli, "users_with_memories", lambda db: [replayed, other])

    assert cli.main(["--all"]) == 0

    assert len(_insights(db, replayed)) == 1
    assert len(_insights(db, other)) == 1
    out = capsys.readouterr().out
    assert str(replayed) in out and str(other) in out
    assert "2 account(s); 2 insight(s) derived" in out


def test_discovery_finds_an_account_that_has_memories(db, replayed, cli):
    """The other half, as a single cheap query rather than a sweep."""
    assert replayed in cli.users_with_memories(db)


def test_user_touches_only_that_account(db, replayed, cli):
    other = new_user()
    quiet = IngestionService(db, FakeModelProvider([]), default_tz=TZ)
    quiet.ingest_events(other, _phase("2026-05-01", 8, 40.0, "x-a"))
    quiet.ingest_events(other, _phase("2026-05-09", 8, 90.0, "x-b"))

    cli.main(["--user", str(replayed)])

    assert len(_insights(db, replayed)) == 1
    assert _insights(db, other) == []


def test_user_and_all_are_mutually_exclusive_and_one_is_required(cli):
    for argv in ([], ["--user", str(UUID(int=1)), "--all"]):
        with pytest.raises(SystemExit):
            cli.main(argv)


def test_an_account_with_nothing_consolidatable_is_reported_not_crashed(db, cli, capsys):
    empty = new_user()
    assert cli.main(["--user", str(empty)]) == 0
    assert "derived 0" in capsys.readouterr().out


def test_user_discovery_ignores_accounts_that_only_hold_insights(db, replayed, cli):
    """`--all` must not grow with its own output."""
    cli.main(["--user", str(replayed)])
    with db.transaction() as cur:
        cur.execute(
            "DELETE FROM memories WHERE user_id = %s AND type <> 'insight'", [replayed]
        )
    assert replayed not in cli.users_with_memories(db)


def test_one_failing_account_does_not_end_the_sweep(db, replayed, cli, capsys, monkeypatch):
    """An operator command over many accounts must report a bad one and keep going."""
    def explode(self, *a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(ConsolidationService, "consolidate", explode)
    assert cli.main(["--user", str(replayed)]) == 1
    out = capsys.readouterr().out
    assert "FAILED" in out and "1 account(s) failed" in out


# ══ one implementation, not two ════════════════════════════════════════════════════════
def test_the_cli_holds_no_consolidation_logic():
    """A second copy of the identity rule would be a second place duplicate insights can be
    born, which is exactly what I-12 exists to prevent."""
    source = inspect.getsource(consolidate_cli)
    for forbidden in (
        "fingerprint", "detect_level_shifts", "detect_intervention_outcomes", "collapse(",
        "insert_memories", "mark_superseded", "mark_retracted", "INSERT", "UPDATE",
        "CONSOLIDATION_SERIES",
    ):
        assert forbidden not in source, f"{forbidden} must live in the service, not the CLI"


def test_the_cli_builds_the_same_service_the_app_builds():
    """Same class, same construction, so the CLI and the runtime execute identical logic."""
    cli_src = inspect.getsource(consolidate_cli)
    assert "ConsolidationService(db, default_tz=settings.default_tz)" in cli_src

    import api.main as api_main

    assert "ConsolidationService(db, default_tz=settings.default_tz)" in inspect.getsource(
        api_main
    )


def test_no_model_provider_is_needed():
    """Consolidation makes no model call, so the sweep runs with no Bedrock access at all —
    which is also why insights land unembedded (I-16)."""
    source = inspect.getsource(consolidate_cli)
    assert "build_default_provider" not in source
    assert "ModelProvider" not in source


# ══ I-18 / I-16 — what the retroactive rows actually say ═══════════════════════════════
def test_retroactive_insights_carry_a_truthful_created_at(db, replayed, cli):
    """**I-18**, ADR-13.10. The claim is *about* May; it was *learned* now. Back-dating it
    would be the replay clock this project rejected, reintroduced through the back door."""
    before = datetime.now(timezone.utc)
    cli.main(["--user", str(replayed)])

    (insight,) = _insights(db, replayed)
    assert insight["created_at"] >= before                    # learned now, truthfully
    assert insight["event_time"] < before - timedelta(days=1)  # about then
    assert insight["payload"]["window_end"].startswith("2026-05")


def test_retroactive_insights_are_written_unembedded(db, replayed, cli):
    """**I-16.** The existing backfill picks them up; the sweep needs no model."""
    cli.main(["--user", str(replayed)])
    (insight,) = _insights(db, replayed)
    assert insight["embedded"] is False


def test_a_claim_over_reconstructed_evidence_inherits_its_confidence(db, replayed, cli):
    """An insight is exactly as trustworthy as the least trustworthy row under it."""
    cli.main(["--user", str(replayed)])
    (insight,) = _insights(db, replayed)
    assert insight["confidence"] == pytest.approx(0.6)
