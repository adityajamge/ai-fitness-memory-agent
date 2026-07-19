"""Day-one canary #2 (T2): LangGraph checkpointing on CockroachDB.

Proves the compatibility bet from ADR-13.8 (outside voice #10). Canary OUTCOME
(2026-07-17): stock PostgresSaver FAILS on CockroachDB — its read query uses
an unaliased-SRF column prefix and 2-D bytea arrays, both rejected (details in
agent/checkpointer.py). The recorded fallback is `CockroachDBSaver`, a thin
subclass rewriting only the read path; this canary tests it, exercising:

  1. `.setup()` — the saver's DDL migrations (ran unmodified).
  2. `put()` with BOTH kinds of channel values: a primitive (inlined into the
     checkpoint jsonb) and a non-primitive (written to checkpoint_blobs) — the
     blob path is the one the rewritten read query joins against.
  3. `get_tuple()` / `list()` — the rewritten read path, round-tripping both
     channel values and metadata for the latest checkpoint of a thread.

Permanent CI test; same DATABASE_URL / skip / REQUIRE_DB semantics as
engine/tests/test_vector_canary.py (a canary must never silently skip in CI).
Full investigation + maintenance notes: docs/engineering/cockroachdb-postgressaver.md.
"""

import os
import uuid

import psycopg
import pytest

from agent.checkpointer import CockroachDBSaver

# 127.0.0.1, not localhost: psycopg tries ::1 first and Windows takes ~130s to
# give up when the node listens on IPv4 only.
DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://root@127.0.0.1:26257/defaultdb?sslmode=disable"
)
_DB_REQUIRED = bool(os.environ.get("CI") or os.environ.get("REQUIRE_DB"))


def _require_db():
    try:
        psycopg.connect(DATABASE_URL, connect_timeout=5).close()
    except psycopg.OperationalError as exc:
        if _DB_REQUIRED:
            raise AssertionError(
                f"CI/REQUIRE_DB is set but CockroachDB is unreachable at {DATABASE_URL}: {exc}"
            ) from exc
        pytest.skip(f"no CockroachDB reachable at {DATABASE_URL} ({exc}); set REQUIRE_DB=1 to fail")


def _checkpoint(checkpoint_id: str, value: str, blob_version: int) -> dict:
    return {
        "v": 4,
        "id": checkpoint_id,
        "ts": "2026-07-17T00:00:00+00:00",
        # A str is inlined into the checkpoint row; a list forces the
        # checkpoint_blobs write + the blob join in the read query. Blobs are
        # immutable per (channel, version), so a changed value means a bumped
        # version — real LangGraph semantics.
        "channel_values": {"canary_str": value, "canary_blob": [value, 42]},
        "channel_versions": {"canary_str": 1, "canary_blob": blob_version},
        "versions_seen": {"__input__": {}},
    }


def test_postgres_saver_setup_write_read():
    _require_db()
    thread_id = f"canary-{uuid.uuid4().hex[:12]}"
    config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
    # Sequential ids so "latest checkpoint" ordering is unambiguous.
    id_1 = "00000000-0000-0000-0000-000000000001"
    id_2 = "00000000-0000-0000-0000-000000000002"

    with CockroachDBSaver.from_conn_string(DATABASE_URL) as saver:
        saver.setup()  # DDL migrations — the compatibility bet itself
        try:
            saver.put(config, _checkpoint(id_1, "first", 1), {"source": "canary", "step": 1},
                      {"canary_str": 1, "canary_blob": 1})
            saver.put(config, _checkpoint(id_2, "second", 2), {"source": "canary", "step": 2},
                      {"canary_blob": 2})

            latest = saver.get_tuple({"configurable": {"thread_id": thread_id}})
            assert latest is not None, "no checkpoint came back for the thread"
            assert latest.checkpoint["id"] == id_2
            assert latest.checkpoint["channel_values"]["canary_str"] == "second"
            assert latest.checkpoint["channel_values"]["canary_blob"] == ["second", 42]
            assert latest.metadata["source"] == "canary"

            history = list(saver.list({"configurable": {"thread_id": thread_id}}))
            assert [t.checkpoint["id"] for t in history] == [id_2, id_1]
            assert history[1].checkpoint["channel_values"]["canary_blob"] == ["first", 42]
        finally:
            saver.delete_thread(thread_id)  # tables stay (they are the production tables)
