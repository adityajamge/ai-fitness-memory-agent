"""Root pytest conftest — loads `.env` before any test module is imported.

Test fixtures read `DATABASE_URL` (and `REQUIRE_DB`) from `os.environ` directly at import
time rather than through `engine.config.load_settings()`, because they need the value before
an app exists. Without this hook a developer who has configured `.env` — the documented way
to point at CockroachDB Cloud — would see every database-backed test fall back to the local
default and fail confusingly.

pytest imports conftest.py files from the rootdir downward before collecting test modules,
so this runs first. Real environment variables still win (`override=False`), which keeps
`DATABASE_URL=... pytest` and CI's own variables authoritative.
"""

from __future__ import annotations

from engine.config import _load_dotenv_if_present

_load_dotenv_if_present()
