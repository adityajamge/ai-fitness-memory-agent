"""Shared fixtures for engine tests: a real single-node CockroachDB (ADR-13.8) and a
deterministic fake model provider (Bedrock is mocked behind the injected interface
everywhere except the live-model eval lane — 12-test-plan.md).

DB reachability follows the canary convention: SKIP with a visible reason when no database
is reachable, unless CI/REQUIRE_DB is set, in which case FAIL.
"""

from __future__ import annotations

import math
import os
import uuid

import psycopg
import pytest

from engine.db import Database
from engine.model import EmbeddingError, ExtractedEvent, ExtractionError

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://root@127.0.0.1:26257/defaultdb?sslmode=disable"
)
_DB_REQUIRED = bool(os.environ.get("CI") or os.environ.get("REQUIRE_DB"))
_DIMS = 512


def _unit_vector(seed_text: str) -> list[float]:
    """Deterministic normalized 512-dim vector derived from text (stands in for Titan V2)."""
    seed = sum(ord(c) for c in seed_text) + 1
    raw = [math.sin(seed * 0.017 + i * 0.031) for i in range(_DIMS)]
    norm = math.sqrt(sum(x * x for x in raw)) or 1.0
    return [x / norm for x in raw]


class FakeModelProvider:
    """Configurable stand-in for a real ModelProvider.

    ``events`` is returned by extract_events; toggles inject the failure modes the
    transaction-boundaries doc enumerates. Attributes are mutable so a test can flip
    behavior between calls (e.g. fail, then succeed on reprocess)."""

    def __init__(
        self,
        events: list[ExtractedEvent] | None = None,
        *,
        extract_error: bool = False,
        fail_first: bool = False,
        embed_error: bool = False,
    ) -> None:
        self.events = events or []
        self.extract_error = extract_error
        self.fail_first = fail_first
        self.embed_error = embed_error
        self.extract_calls = 0
        self.embed_calls = 0

    def extract_events(self, text: str, *, now, tz) -> list[ExtractedEvent]:
        self.extract_calls += 1
        if self.extract_error:
            raise ExtractionError("forced extraction failure")
        if self.fail_first and self.extract_calls == 1:
            raise ExtractionError("forced first-attempt failure")
        return list(self.events)

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.embed_calls += 1
        if self.embed_error:
            raise EmbeddingError("forced embedding failure")
        return [_unit_vector(t) for t in texts]


@pytest.fixture(scope="session")
def db() -> Database:
    database = Database(DATABASE_URL)
    try:
        database.setup_schema()
    except psycopg.OperationalError as exc:
        if _DB_REQUIRED:
            raise AssertionError(
                f"CI/REQUIRE_DB set but CockroachDB unreachable at {DATABASE_URL}: {exc}"
            ) from exc
        pytest.skip(f"no CockroachDB reachable at {DATABASE_URL} ({exc}); set REQUIRE_DB=1 to fail")
    return database


@pytest.fixture()
def user_id() -> uuid.UUID:
    """A fresh user per test — isolation without per-test DDL (every query is user-scoped)."""
    return uuid.uuid4()
