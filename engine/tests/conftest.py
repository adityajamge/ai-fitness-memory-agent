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
from engine.model import (
    EmbeddingError,
    ExtractedEvent,
    ExtractionError,
    NarrationError,
    NutritionError,
    PlanningError,
    ToolCall,
    ToolSpec,
)
from engine.tests.dbcleanup import register_user

#: Test-suite database URL.
#:
#: ``DATABASE_URL`` has already been redirected to ``DATABASE_URL_TEST_ONLY`` (when set) by the
#: **root conftest**, for the whole session — see its docstring for why that redirect must
#: happen there rather than here. Reading the redirected variable keeps this module's behavior
#: identical to before while guaranteeing every consumer, including the FastAPI app that
#: ``api/tests`` builds through ``load_settings()``, resolves the same test cluster.
#:
#: **The suite must never run against the cluster holding the real replayed history.** Every
#: id below is minted fresh per test, and ``memories`` has no foreign key to ``users``, so rows
#: written here are orphans no cascade would reach. Since Phase 5 M0 they are registered with
#: ``engine/tests/dbcleanup.py`` and purged at session end, which stops a run from permanently
#: growing the cluster — but the purge deletes *only ids this run minted*, so it is a hygiene
#: mechanism, **not** a licence to point the suite at real data.
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
    behavior between calls (e.g. fail, then succeed on reprocess).

    It honors the empty-result contract (``engine/model.py``): an empty ``events`` list is
    the *affirmed-contentless* case ("thanks!") and returns ``[]``, while ``extract_error``
    models everything else — a failed call or a turn the provider could not type — by
    raising ``ExtractionError``. There is deliberately no toggle for "returns [] on real
    content": that is the contract violation `BedrockProvider` now prevents
    (agent/tests/test_bedrock_provider.py), not a behavior the engine must tolerate."""

    def __init__(
        self,
        events: list[ExtractedEvent] | None = None,
        *,
        extract_error: bool = False,
        fail_first: bool = False,
        embed_error: bool = False,
        plan_calls: list[ToolCall] | None = None,
        plan_error: bool = False,
        narration: str | None = None,
        narrate_error: bool = False,
        nutrition: list[dict] | None = None,
        nutrition_error: bool = False,
    ) -> None:
        self.events = events or []
        self.extract_error = extract_error
        self.fail_first = fail_first
        self.embed_error = embed_error
        # Stage (B½). Default None means "this fake estimates nothing" — it raises, which is
        # the *failure* path, so every pre-nutrition test keeps writing meals with no nutrition
        # key and asserting exactly what it asserted before. Opting in is explicit.
        self.nutrition = nutrition
        self.nutrition_error = nutrition_error
        self.nutrition_calls = 0
        self.last_nutrition_items: list[dict] = []
        # Phase 3 (M4) agent surfaces — scripted so the M5 graph tests can drive routing
        # (which tools plan returns) and narration deterministically.
        self.plan_calls = plan_calls if plan_calls is not None else []
        self.plan_error = plan_error
        self.narration = narration
        self.narrate_error = narrate_error
        self.extract_calls = 0
        self.embed_calls = 0
        self.plan_invocations = 0
        self.narrate_invocations = 0
        self.last_tools: list[ToolSpec] = []

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

    def estimate_nutrition(self, items: list[dict], *, context: str = "") -> list[dict]:
        self.nutrition_calls += 1
        self.last_nutrition_items = list(items)
        if self.nutrition_error or self.nutrition is None:
            raise NutritionError("forced nutrition failure")
        return [dict(c) for c in self.nutrition]

    nutrition_model_id = "fake-model"
    nutrition_prompt_version = "fake-prompt/v0"

    def plan(self, question: str, tools: list[ToolSpec], *, now, tz) -> list[ToolCall]:
        self.plan_invocations += 1
        self.last_tools = list(tools)
        if self.plan_error:
            raise PlanningError("forced planning failure")
        return list(self.plan_calls)

    def narrate(self, question: str, context) -> str:
        self.narrate_invocations += 1
        if self.narrate_error:
            raise NarrationError("forced narration failure")
        if self.narration is not None:
            return self.narration
        # Default: an honest, deterministic answer that cites every citable ID, so tests
        # can assert citation pass-through without pinning exact prose.
        ids = sorted(str(i) for i in context.citable_ids())
        if not ids:
            return "I have no logged data for that yet."
        return "Based on your logged data: " + " ".join(f"[{i}]" for i in ids)


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
    """A fresh user per test — isolation without per-test DDL (every query is user-scoped).

    Registered for the end-of-session purge (M0): this is one of the two choke points through
    which every test-owned row enters the database, so registering here means no test has to
    remember to clean up and no new test can forget to."""
    return register_user(uuid.uuid4())
