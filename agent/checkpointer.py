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

This module is ALSO the enforcement point for the graph-state durability
boundary (decision M5-1, ADR-13.14): see ``_GuardedSerde`` below.
"""

from base64 import b64decode
from typing import Any

from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from engine.assembly import ContextBlock, RetrievalOutcome
from engine.ingestion import Receipt
from engine.trace import EvidenceTrace

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


# ── the graph-state durability boundary (M5-1 L2 — the architectural enforcement point) ──
#
# Heavyweight, turn-local runtime objects must NEVER enter the checkpointed graph state:
# the checkpointer holds conversation/execution continuity only (ADR-13.14 — the trace's
# durable home is the `evidence_traces` table, Phase 6), and blobs in graph state are a
# known checkpointer footgun (ADR-13.8, strict msgpack).
#
# LangGraph persists every state channel after EVERY super-step, so "clear it before END"
# does not work. The guard therefore lives on the serialization path, which is the single
# chokepoint every persist flows through — in tests and in production alike. That is what
# makes the invariant a guarantee rather than a convention: adding a banned object to
# `GraphState` does not sneak it past the boundary, it just makes this guard fire.
#
# Verified 2026-07-24 on langgraph 1.2.4 / langgraph-checkpoint 4.1.1 /
# langgraph-checkpoint-postgres 3.1.0: values reach `dumps_typed` as LIVE Python objects
# (containers arrive as live list/dict), and a raise here prevents the write entirely —
# nothing is persisted. Covers `put()` and `put_writes()` in one place.
_BANNED_STATE_TYPES: tuple[type, ...] = (
    ContextBlock,
    EvidenceTrace,
    RetrievalOutcome,
    Receipt,
)

_GUARD_MESSAGE = (
    "{type_name} must never be checkpointed into LangGraph state (decision M5-1, "
    "ADR-13.14): the checkpointer holds conversation continuity only. Keep heavy, "
    "turn-local objects on the per-invocation TurnCarrier (agent/graph.py) instead of a "
    "GraphState channel."
)


class _GuardedSerde:
    """A ``SerializerProtocol`` wrapper that refuses to serialize banned runtime objects.

    Delegates everything to the real serde; the only added behavior is a sweep of each
    value before it is written — the value itself plus one level into containers, which is
    where an accidental ``{"trace": [trace]}`` channel would hide. Deliberately raises
    ``TypeError`` (not a warning, not a drop): a silently discarded trace would be worse
    than a loud failure.
    """

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    @property
    def inner(self) -> Any:
        """The wrapped serde (tests assert the guard is installed exactly once)."""
        return self._inner

    @staticmethod
    def _reject(value: Any) -> None:
        if isinstance(value, _BANNED_STATE_TYPES):
            raise TypeError(_GUARD_MESSAGE.format(type_name=type(value).__name__))

    def _sweep(self, value: Any) -> None:
        self._reject(value)
        if isinstance(value, (list, tuple, set, frozenset)):
            for item in value:
                self._reject(item)
        elif isinstance(value, dict):
            for item in value.values():
                self._reject(item)

    # ── SerializerProtocol ────────────────────────────────────────────────────────────
    def dumps_typed(self, obj: Any) -> tuple[str, bytes]:
        self._sweep(obj)
        return self._inner.dumps_typed(obj)

    def dumps(self, obj: Any) -> bytes:
        self._sweep(obj)
        return self._inner.dumps(obj)

    def loads_typed(self, data: tuple[str, bytes]) -> Any:
        return self._inner.loads_typed(data)

    def loads(self, data: bytes) -> Any:
        return self._inner.loads(data)


class _CockroachReads:
    """Mixin: CockroachDB-safe read query + jsonb/base64 loaders, plus the M5-1 L2 guard.

    The guard is installed in ``__init__`` so it is on by construction for every saver this
    app builds — including via ``from_conn_string``, which routes through ``cls(...)``.
    """

    SELECT_SQL = SELECT_SQL

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # Idempotent: re-wrapping would still be correct, but a single layer keeps the
        # `inner` assertion in the guard tests meaningful.
        if not isinstance(self.serde, _GuardedSerde):
            self.serde = _GuardedSerde(self.serde)

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
