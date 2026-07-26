"""M5-1 L2: the graph-state durability boundary, enforced on the serialization path.

The invariant under test: `ContextBlock`, `EvidenceTrace`, `RetrievalOutcome`, and `Receipt`
**cannot** be checkpointed (ADR-13.14 — the checkpointer holds conversation continuity only;
the trace's durable home is `evidence_traces`, Phase 6).

These tests exercise the real `CockroachDBSaver` against a real database, because the point
of L2 is that the guarantee holds on the *actual persist path*, not in a mock: a raise must
prevent the write, and nothing may survive in the checkpoint afterwards. Same
skip-unless-CI/REQUIRE_DB convention as the canaries.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import psycopg
import pytest

from agent.checkpointer import (
    AsyncCockroachDBSaver,
    CockroachDBSaver,
    _CockroachReads,
    _GuardedSerde,
)
from engine.assembly import ContextBlock, RetrievalOutcome
from engine.ingestion import Receipt
from engine.retrieval import AggregateResult, AggregateSpec
from engine.trace import EvidenceTrace, RetrievalStep

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://root@127.0.0.1:26257/defaultdb?sslmode=disable"
)
_DB_REQUIRED = bool(os.environ.get("CI") or os.environ.get("REQUIRE_DB"))
UTC = timezone.utc
_META = {"source": "loop", "step": 0, "parents": {}}


# ── the banned objects, one of each ───────────────────────────────────────────────────
def _trace() -> EvidenceTrace:
    return EvidenceTrace(
        trace_id=uuid.uuid4(),
        question="q",
        retrieval_steps=(),
        evidence=(),
        insights=(),
        timeline=(),
        ranking=(),
        assembled_at=datetime.now(UTC),
    )


def _context() -> ContextBlock:
    return ContextBlock(question="q", aggregates=(), counts=(), memories=(), omitted_count=0)


def _outcome() -> RetrievalOutcome:
    spec = AggregateSpec(
        metric="protein_g",
        start=datetime(2026, 7, 1, tzinfo=UTC),
        end=datetime(2026, 8, 1, tzinfo=UTC),
        tz="UTC",
    )
    return RetrievalOutcome(
        result=AggregateResult(spec=spec, buckets=()),
        step=RetrievalStep(family="aggregate", sql="SELECT 1", params={}, row_count=0),
    )


def _receipt() -> Receipt:
    return Receipt()


BANNED_FACTORIES = [
    pytest.param(_trace, id="EvidenceTrace"),
    pytest.param(_context, id="ContextBlock"),
    pytest.param(_outcome, id="RetrievalOutcome"),
    pytest.param(_receipt, id="Receipt"),
]


@pytest.fixture(scope="module")
def saver():
    try:
        psycopg.connect(DATABASE_URL, connect_timeout=5).close()
    except psycopg.OperationalError as exc:
        if _DB_REQUIRED:
            raise AssertionError(
                f"CI/REQUIRE_DB set but CockroachDB unreachable at {DATABASE_URL}: {exc}"
            ) from exc
        pytest.skip(f"no CockroachDB reachable at {DATABASE_URL} ({exc}); set REQUIRE_DB=1 to fail")
    with CockroachDBSaver.from_conn_string(DATABASE_URL) as built:
        built.setup()
        yield built


def _config(kind: str) -> dict:
    return {
        "configurable": {"thread_id": f"guard-{kind}-{uuid.uuid4().hex[:8]}", "checkpoint_ns": ""}
    }


def _checkpoint(channel_values: dict) -> dict:
    """A checkpoint in the shape LangGraph really writes (same construction as the T2
    canary): ``channel_versions`` must match the values, or the blob join finds nothing on
    read."""
    return {
        "v": 4,
        "id": str(uuid.uuid4()),
        "ts": datetime.now(UTC).isoformat(),
        "channel_values": channel_values,
        "channel_versions": _versions(channel_values),
        "versions_seen": {"__input__": {}},
    }


def _versions(channel_values: dict) -> dict:
    return {name: 1 for name in channel_values}


# ── the guard is installed by construction ────────────────────────────────────────────
def test_guard_is_installed_on_every_saver(saver) -> None:
    # Not opt-in: building the saver at all puts the boundary in place.
    assert isinstance(saver.serde, _GuardedSerde)
    assert not isinstance(saver.serde.inner, _GuardedSerde)  # exactly one layer


def test_both_savers_share_the_guard_installation() -> None:
    # The boundary must hold whichever saver the app builds: both concrete classes inherit
    # the guarding __init__ from the shared mixin (no connection needed to assert this).
    assert _CockroachReads in CockroachDBSaver.__mro__
    assert _CockroachReads in AsyncCockroachDBSaver.__mro__
    assert CockroachDBSaver.__init__ is _CockroachReads.__init__
    assert AsyncCockroachDBSaver.__init__ is _CockroachReads.__init__


# ── every banned type is refused, at the top level of a channel ───────────────────────
@pytest.mark.parametrize("factory", BANNED_FACTORIES)
def test_banned_type_cannot_be_checkpointed(saver, factory) -> None:
    config = _config("top")
    with pytest.raises(TypeError) as excinfo:
        saver.put(config, _checkpoint({"heavy": factory()}), _META, {"heavy": 1})

    # The message must teach: it names the invariant and where the object belongs instead.
    message = str(excinfo.value)
    assert "M5-1" in message and "ADR-13.14" in message
    assert "TurnCarrier" in message
    # ...and the write really did not happen.
    assert saver.get_tuple(config) is None


@pytest.mark.parametrize("container", ["list", "tuple", "dict", "set"])
def test_banned_type_nested_one_level_is_refused(saver, container) -> None:
    trace = _trace()
    value = {
        "list": [trace],
        "tuple": (trace,),
        "dict": {"t": trace},
        "set": {trace},  # frozen dataclass → hashable
    }[container]
    config = _config(f"nested-{container}")

    with pytest.raises(TypeError, match="M5-1"):
        saver.put(config, _checkpoint({"heavy": value}), _META, {"heavy": 1})
    assert saver.get_tuple(config) is None


def test_pending_writes_are_guarded_too(saver) -> None:
    # put_writes is a second persist path; avenue (a) covers it in the same place.
    config = _config("writes")
    config["configurable"]["checkpoint_id"] = str(uuid.uuid4())
    with pytest.raises(TypeError, match="M5-1"):
        saver.put_writes(config, [("heavy", _trace())], str(uuid.uuid4()))


# ── the guard is invisible to legitimate state ────────────────────────────────────────
def test_allowed_state_round_trips_unchanged(saver) -> None:
    config = _config("ok")
    values = {
        "messages": ["hello", "world"],
        "question": "how much protein today?",
        "user_id": str(uuid.uuid4()),
        "citations": [str(uuid.uuid4()), str(uuid.uuid4())],
        "tool_calls": [{"tool": "aggregate_memories", "arguments": {"metric": "protein_g"}}],
    }
    saver.put(config, _checkpoint(values), _META, _versions(values))

    loaded = saver.get_tuple(config)
    assert loaded is not None
    for key, expected in values.items():
        assert loaded.checkpoint["channel_values"][key] == expected


def test_guard_does_not_break_the_canary_shape(saver) -> None:
    # A plain conversational checkpoint still writes and reads (the T2 canary property).
    config = _config("canary")
    saver.put(config, _checkpoint({"messages": ["hi"]}), _META, {"messages": 1})
    assert saver.get_tuple(config).checkpoint["channel_values"]["messages"] == ["hi"]


# ── unit-level: the wrapper itself ────────────────────────────────────────────────────
def test_serde_wrapper_delegates_and_rejects() -> None:
    class _Inner:
        def __init__(self):
            self.dumped = []

        def dumps_typed(self, obj):
            self.dumped.append(obj)
            return ("json", b"{}")

        def dumps(self, obj):
            self.dumped.append(obj)
            return b"{}"

        def loads_typed(self, data):
            return "loaded"

        def loads(self, data):
            return "loaded"

    inner = _Inner()
    guard = _GuardedSerde(inner)

    assert guard.dumps_typed("fine") == ("json", b"{}")
    assert guard.loads_typed(("json", b"{}")) == "loaded"
    assert guard.loads(b"{}") == "loaded"
    assert inner.dumped == ["fine"]

    for call in (guard.dumps_typed, guard.dumps):
        with pytest.raises(TypeError, match="must never be checkpointed"):
            call(_trace())
    assert inner.dumped == ["fine"]  # nothing banned ever reached the real serde
