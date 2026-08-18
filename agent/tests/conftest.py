"""Agent test fixtures — reuses the engine suite's real-CockroachDB `db` fixture and the
deterministic FakeModelProvider (same skip-unless-CI/REQUIRE_DB convention, 12-test-plan.md).
Importing the fixture functions into this conftest registers them for agent/tests (same
pattern as cli/tests/conftest.py).

Also provides the shared graph harness (`saver`, `graph_db`, `make_graph`) so a test module
that needs a real compiled graph against a real checkpointer does not have to rebuild one.
`test_graph_routing.py` predates this and defines its own module-level copies, which pytest
resolves in that module's favour — leaving it untouched was deliberate, since it is the
tripwire suite for the durability boundary.
"""

from __future__ import annotations

import os
import uuid

import psycopg
import pytest

from agent.checkpointer import CockroachDBSaver
from agent.graph import build_graph
from engine.consolidation import ConsolidationService
from engine.db import Database
from engine.ingestion import IngestionService
from engine.tests.conftest import (  # noqa: F401  (re-exported pytest fixtures)
    DATABASE_URL,
    FakeModelProvider,
    db,
    user_id,
)

#: Read at import time like every other fixture here — the root conftest has already
#: redirected DATABASE_URL to DATABASE_URL_TEST_ONLY by then, which is what keeps the suite
#: off the cluster holding the real replayed history.
GRAPH_DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://root@127.0.0.1:26257/defaultdb?sslmode=disable"
)
_DB_REQUIRED = bool(os.environ.get("CI") or os.environ.get("REQUIRE_DB"))
IST = "Asia/Kolkata"


@pytest.fixture(scope="module")
def saver():
    """The real CockroachDBSaver, guard included — never a stub. A graph test that fakes the
    checkpointer proves nothing about the boundary it is supposed to be defending."""
    try:
        psycopg.connect(GRAPH_DATABASE_URL, connect_timeout=5).close()
    except psycopg.OperationalError as exc:
        if _DB_REQUIRED:
            raise AssertionError(
                f"CI/REQUIRE_DB set but CockroachDB unreachable at {GRAPH_DATABASE_URL}: {exc}"
            ) from exc
        pytest.skip(
            f"no CockroachDB reachable at {GRAPH_DATABASE_URL} ({exc}); set REQUIRE_DB=1 to fail"
        )
    database = Database(GRAPH_DATABASE_URL)
    database.setup_schema()
    with CockroachDBSaver.from_conn_string(GRAPH_DATABASE_URL) as built:
        built.setup()
        yield built


@pytest.fixture(scope="module")
def graph_db(saver) -> Database:  # noqa: ARG001 — ordering: schema is applied by `saver`
    return Database(GRAPH_DATABASE_URL)


@pytest.fixture()
def make_graph(saver, graph_db):
    """Build a graph around a scripted provider; returns (graph, provider)."""

    def _build(provider, *, consolidation: bool = False):
        ingestion = IngestionService(graph_db, provider, default_tz=IST)
        return (
            build_graph(
                db=graph_db,
                model=provider,
                ingestion=ingestion,
                checkpointer=saver,
                default_tz=IST,
                consolidation=(
                    ConsolidationService(graph_db, provider, default_tz=IST)
                    if consolidation
                    else None
                ),
            ),
            provider,
        )

    return _build


@pytest.fixture()
def thread_id() -> str:
    """A fresh conversation thread per test — history must never bleed between tests."""
    return f"t-{uuid.uuid4().hex[:12]}"
