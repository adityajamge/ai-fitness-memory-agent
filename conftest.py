"""Root pytest conftest — loads `.env`, then forces the whole session onto the test database.

Test fixtures read `DATABASE_URL` (and `REQUIRE_DB`) from `os.environ` directly at import
time rather than through `engine.config.load_settings()`, because they need the value before
an app exists. Without this hook a developer who has configured `.env` — the documented way
to point at CockroachDB Cloud — would see every database-backed test fall back to the local
default and fail confusingly.

pytest imports conftest.py files from the rootdir downward before collecting test modules,
so this runs first. Real environment variables still win (`override=False`), which keeps
`DATABASE_URL=... pytest` and CI's own variables authoritative.

**`DATABASE_URL_TEST_ONLY` is applied here, at the root, and deliberately by overwriting
`DATABASE_URL` for the session.** Doing it per-fixture is not enough and was an actual bug:
`api/tests/` builds the real FastAPI app through `create_app()`, which calls
`load_settings()` and reads `DATABASE_URL` itself — so the app under test connected to the
production cluster even while the engine fixtures were correctly pointed at the test one.
Any future code path that resolves the database independently (a new CLI, another app
factory) is covered by this too, because the variable it reads has already been redirected.

The suite writes hundreds of rows per run (`user_id` is a fresh UUID per test and `memories`
has no FK to `users`), so pointing it at a cluster holding real data is destructive rather
than merely untidy. Since Phase 5 M0 those rows are purged at session end by the fixture
below — that is hygiene, **not** a reason to relax the rule above.
"""

from __future__ import annotations

import os
import warnings

import pytest

from engine.config import _load_dotenv_if_present

_load_dotenv_if_present()

_test_only = (os.environ.get("DATABASE_URL_TEST_ONLY") or "").strip()
if _test_only:
    os.environ["DATABASE_URL"] = _test_only


@pytest.fixture(scope="session", autouse=True)
def _purge_test_rows():
    """Delete every row this run created, once, after the last test (Phase 5 M0).

    Lives at the root rather than in `engine/tests/conftest.py` so it covers all four test
    packages with one registration, and so it runs after *everything* — including api tests,
    whose accounts are created by the app rather than by a fixture.

    **It reports rather than fails.** A session finalizer that errors after a green suite
    obscures the result it is reporting on, and a transient connection hiccup during teardown
    is not a code defect. The hard assertion on the purge's correctness lives in
    `engine/tests/test_db_cleanup.py`, where it can fail precisely; here a leftover row raises
    a visible warning naming the tables, which is enough to notice the mechanism has drifted.

    The import is deferred to fixture-call time because this module is imported before the
    project's packages are importable in some invocations.
    """
    yield

    from engine.tests import dbcleanup

    database_url = os.environ.get(
        "DATABASE_URL", "postgresql://root@127.0.0.1:26257/defaultdb?sslmode=disable"
    )
    if dbcleanup.cleanup_disabled():
        warnings.warn("KEEP_TEST_ROWS set — test rows left in place", stacklevel=1)
        return

    dbcleanup.purge(database_url)
    left = dbcleanup.residue(database_url)
    if left:
        warnings.warn(
            f"test-row purge left residue: {left}. engine/tests/dbcleanup.py no longer "
            "covers every table the suite writes.",
            stacklevel=1,
        )
