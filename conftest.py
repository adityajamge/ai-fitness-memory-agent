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

The suite writes hundreds of rows per run and never cleans up (`user_id` is a fresh UUID per
test and `memories` has no FK to `users`), so pointing it at a cluster holding real data is
destructive rather than merely untidy.
"""

from __future__ import annotations

import os

from engine.config import _load_dotenv_if_present

_load_dotenv_if_present()

_test_only = (os.environ.get("DATABASE_URL_TEST_ONLY") or "").strip()
if _test_only:
    os.environ["DATABASE_URL"] = _test_only
