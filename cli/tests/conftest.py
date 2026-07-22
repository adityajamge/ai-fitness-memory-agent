"""CLI test fixtures — reuses the engine suite's real-CockroachDB `db` fixture and
deterministic FakeModelProvider (same skip-unless-CI/REQUIRE_DB convention, 12-test-plan.md).
Importing the fixture functions into this conftest registers them for cli/tests."""

from __future__ import annotations

from engine.tests.conftest import (  # noqa: F401  (re-exported pytest fixtures)
    DATABASE_URL,
    FakeModelProvider,
    db,
    user_id,
)
