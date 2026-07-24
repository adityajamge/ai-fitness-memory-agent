"""Agent test fixtures — reuses the engine suite's real-CockroachDB `db` fixture and the
deterministic FakeModelProvider (same skip-unless-CI/REQUIRE_DB convention, 12-test-plan.md).
Importing the fixture functions into this conftest registers them for agent/tests (same
pattern as cli/tests/conftest.py)."""

from __future__ import annotations

from engine.tests.conftest import (  # noqa: F401  (re-exported pytest fixtures)
    DATABASE_URL,
    FakeModelProvider,
    db,
    user_id,
)
