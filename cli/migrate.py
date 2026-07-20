"""Manual schema migration runner (D4).

Idempotently applies engine/schema.sql to the database in DATABASE_URL. The app also
runs this on startup (api/main.py lifespan); this CLI is for provisioning a fresh cluster
or re-applying after a schema change without a deploy.

Usage:
    python -m cli.migrate
"""

from __future__ import annotations

from engine.config import load_settings
from engine.db import Database


def main() -> None:
    settings = load_settings()
    db = Database(settings.database_url)
    db.setup_schema()
    print(f"schema applied to {settings.database_url.rsplit('@', 1)[-1]}")


if __name__ == "__main__":
    main()
