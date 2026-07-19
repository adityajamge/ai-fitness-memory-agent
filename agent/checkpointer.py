"""CockroachDB-compatible LangGraph checkpointer (T2 canary outcome, ADR-13.8).

LangGraph's PostgresSaver is *almost* CockroachDB-compatible: `.setup()`
migrations, all write paths (`put`, `put_writes`), and `delete_thread()` run
unmodified. Its read query (SELECT_SQL) contains two Postgres-isms that
CockroachDB rejects:

  1. SRF output columns referenced by function-name prefix
     (`jsonb_each_text.key`) — "no data source matches prefix" (42P01).
     Fix: alias the set-returning function.
  2. `array_agg(array[...])` builds 2-D bytea arrays. CockroachDB's
     multidimensional-array support is incomplete (cockroachdb issue #32552):
     binary result format is unimplemented and even text execution fails with
     "multidimensional arrays must have array expressions with matching
     dimensions". Both are structural — they fail with zero rows.
     Fix: aggregate to jsonb (`jsonb_agg(jsonb_build_array(...))`, blobs
     base64-encoded) and decode in the two loader helpers.

So the ADR-13.8 fallback ("thin hand-rolled checkpointer if the canary
fails") collapses to this subclass: one rewritten read query + two loader
overrides. If upstream renames the result columns, `_load_checkpoint_tuple`
raises KeyError loudly rather than silently misreading.

Known limitation: checkpoints with format `v < 4` would trigger upstream's
SELECT_PENDING_SENDS_SQL (also multidim-array based). This app only ever
writes current-format checkpoints, so that path is unreachable here.

Verified by agent/tests/test_checkpointer_canary.py against single-node
CockroachDB and the real CockroachDB Cloud cluster (2026-07-17).

Full investigation, debugging timeline, and maintenance notes:
docs/engineering/cockroachdb-postgressaver.md (canonical reference).
"""

from base64 import b64decode
from typing import Any

from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

# Same columns as upstream base.SELECT_SQL (consumed by _load_checkpoint_tuple);
# only the two aggregate columns are built differently. The trailing space
# matters: callers concatenate the WHERE clause directly.
SELECT_SQL = """
select
    thread_id,
    checkpoint,
    checkpoint_ns,
    checkpoint_id,
    parent_checkpoint_id,
    metadata,
    (
        select jsonb_agg(jsonb_build_array(bl.channel, bl.type, encode(bl.blob, 'base64')))
        from jsonb_each_text(checkpoint -> 'channel_versions') as cv(key, value)
        inner join checkpoint_blobs bl
            on bl.thread_id = checkpoints.thread_id
            and bl.checkpoint_ns = checkpoints.checkpoint_ns
            and bl.channel = cv.key
            and bl.version = cv.value
    ) as channel_values,
    (
        select jsonb_agg(
            jsonb_build_array(cw.task_id::text, cw.channel, cw.type, encode(cw.blob, 'base64'))
            order by cw.task_id, cw.idx
        )
        from checkpoint_writes cw
        where cw.thread_id = checkpoints.thread_id
            and cw.checkpoint_ns = checkpoints.checkpoint_ns
            and cw.checkpoint_id = checkpoints.checkpoint_id
    ) as pending_writes
from checkpoints """


class _CockroachReads:
    """Mixin: CockroachDB-safe read query + jsonb/base64 loaders."""

    SELECT_SQL = SELECT_SQL

    def _load_blobs(self, blob_values: list[list[str]] | None) -> dict[str, Any]:
        if not blob_values:
            return {}
        return {
            channel: self.serde.loads_typed((type_, b64decode(blob) if blob else b""))
            for channel, type_, blob in blob_values
            if type_ != "empty"
        }

    def _load_writes(self, writes: list[list[str]] | None) -> list[tuple[str, str, Any]]:
        if not writes:
            return []
        return [
            (task_id, channel, self.serde.loads_typed((type_, b64decode(blob) if blob else b"")))
            for task_id, channel, type_, blob in writes
        ]


class CockroachDBSaver(_CockroachReads, PostgresSaver):
    """PostgresSaver with CockroachDB-compatible reads; everything else inherited."""


class AsyncCockroachDBSaver(_CockroachReads, AsyncPostgresSaver):
    """Async variant, same patches (Phase 3 picks sync or async)."""
