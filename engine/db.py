"""Database access seam for the Memory Engine.

``Database`` is the single injectable seam every engine/api/cli caller uses to reach
CockroachDB. Phase 2 opens a fresh connection per unit of work (fine at demo scale); the
same interface can be backed by a connection pool later without touching callers.

Transaction discipline is load-bearing — see
docs/engineering/ingestion-transaction-boundaries.md. In short: model calls happen OUTSIDE
transactions; a write transaction is short, local DB work only.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import psycopg
from psycopg import Connection
from psycopg.rows import dict_row

_SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def _split_statements(sql: str) -> list[str]:
    """Split a DDL script into individual statements.

    Strips ``--`` line comments first (comments may contain semicolons, which would
    otherwise split a statement mid-comment), then splits on ``;``. Safe here because the
    schema has no ``--`` or ``;`` inside string/identifier literals."""
    stripped_lines = []
    for line in sql.splitlines():
        idx = line.find("--")
        stripped_lines.append(line if idx == -1 else line[:idx])
    cleaned = "\n".join(stripped_lines)
    return [stmt.strip() for stmt in cleaned.split(";") if stmt.strip()]


class Database:
    """Owns connection creation and the transaction boundary."""

    def __init__(self, database_url: str) -> None:
        self._url = database_url

    @contextmanager
    def connection(self) -> Iterator[Connection]:
        conn = psycopg.connect(self._url, row_factory=dict_row, connect_timeout=10)
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def transaction(self) -> Iterator[psycopg.Cursor]:
        """Yield a cursor inside one atomic transaction: commit on clean exit, roll back
        on any exception. This is the (D)/(D') boundary from the transaction-boundaries doc."""
        with self.connection() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    yield cur

    def setup_schema(self) -> None:
        """Idempotently create the Phase 2 schema (D4). Safe to run on every app startup
        and from cli/migrate.py. Best-effort enables the vector-index feature flag first;
        on versions where vector indexes are always on, the SET is simply ignored."""
        statements = _split_statements(_SCHEMA_PATH.read_text(encoding="utf-8"))
        with self.connection() as conn:
            conn.autocommit = True  # DDL: each statement stands alone, no wrapping txn
            with conn.cursor() as cur:
                try:
                    cur.execute("SET CLUSTER SETTING feature.vector_index.enabled = true")
                except psycopg.Error:
                    pass  # setting absent where vector indexes are on by default
                for stmt in statements:
                    cur.execute(stmt)
