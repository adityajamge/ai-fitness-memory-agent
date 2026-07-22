"""API test fixtures: a TestClient wired to a fake model provider over real CockroachDB.

Same DB-reachability convention as the engine tests (skip unless CI/REQUIRE_DB). The fake
provider means no Bedrock calls in CI (12-test-plan.md); ingestion still exercises the full
write path against a real database."""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import psycopg
import pytest
from fastapi.testclient import TestClient

from api.main import create_app
from engine.model import ExtractedEvent
from engine.tests.conftest import FakeModelProvider

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://root@127.0.0.1:26257/defaultdb?sslmode=disable"
)
_DB_REQUIRED = bool(os.environ.get("CI") or os.environ.get("REQUIRE_DB"))


def unique_email() -> str:
    return f"user-{uuid.uuid4().hex}@example.com"


def _require_db() -> None:
    try:
        psycopg.connect(DATABASE_URL, connect_timeout=5).close()
    except psycopg.OperationalError as exc:
        if _DB_REQUIRED:
            raise AssertionError(
                f"CI/REQUIRE_DB set but CockroachDB unreachable at {DATABASE_URL}: {exc}"
            ) from exc
        pytest.skip(f"no CockroachDB reachable at {DATABASE_URL} ({exc}); set REQUIRE_DB=1 to fail")


def _meal_provider() -> FakeModelProvider:
    return FakeModelProvider(
        [
            ExtractedEvent(
                type="meal",
                event_time=datetime(2026, 7, 20, 13, 0, tzinfo=timezone.utc),
                tz="Asia/Kolkata",
                confidence=0.9,
                summary="lunch: 250g curd, 3 eggs, 200g chicken",
                payload={
                    "meal_type": "lunch",
                    "items": [{"name": "curd", "qty_g": 250}],
                    "nutrition": {"protein_g": 78, "kcal": 720, "estimated": True},
                },
            )
        ]
    )


@pytest.fixture()
def app_provider() -> FakeModelProvider:
    """The app's injected provider. Tests that need to flip failure modes between calls
    (e.g. fail an ingest to create a note, then reprocess it successfully) request this
    alongside `client` and mutate its attributes — the fake is mutable by design."""
    return _meal_provider()


@pytest.fixture()
def client(app_provider):
    _require_db()
    app = create_app(provider=app_provider)
    with TestClient(app) as test_client:
        yield test_client
