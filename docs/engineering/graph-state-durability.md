# Graph-State Durability: keeping heavy objects out of the checkpoint

> Canonical reference for the enforcement layer in `agent/checkpointer.py` (`_GuardedSerde`)
> and `agent/graph.py` (`GraphState`, `TurnCarrier`, `_checked`). Decision:
> [ADR-14.9](../office-hours/09-decisions.md#adr-14), refining
> [ADR-13.14](../office-hours/09-decisions.md#adr-13). Companion doc for the checkpointer
> itself: [cockroachdb-postgressaver.md](cockroachdb-postgressaver.md).

## The invariant

Four runtime types must **never** enter checkpointed LangGraph state:

`ContextBlock` · `EvidenceTrace` · `RetrievalOutcome` · `Receipt`

Two independent reasons:

1. **Architectural (ADR-13.14).** The checkpointer holds *conversation and execution
   continuity*. The trace's durable home is the `evidence_traces` table (T7), and the UI reads
   it from there. A trace living in two places means two sources of truth and a silent
   divergence the glass box would never reveal.
2. **Mechanical (ADR-13.8).** The checkpointer's serde is strict msgpack, and blobs in graph
   state are a documented footgun of this layer. A nested frozen-dataclass tree carrying SQL
   strings and evidence rows is exactly the payload that turns a working checkpoint into a
   deserialization failure on the next read.

## Why "clear it before END" does not work

The obvious design — keep the objects in state, null them out in the last node — is wrong,
and understanding why is what drove everything else. **LangGraph persists every state channel
after every super-step**, not once at the end of a turn. A heavy object placed in a channel is
serialized on *each* node transition, so by the time a final node cleared it, it would already
have been written several times.

The objects therefore need somewhere else to live for the duration of a turn.

## The design: reference-only state + a turn-scoped carrier

| | Checkpointed `GraphState` | Per-invocation `TurnCarrier` |
|---|---|---|
| Holds | `messages`, `user_id`, `question`, `now`, `tz`, `tool_calls`, `answer`, `citations` | `outcomes`, `context`, `trace`, `receipts`, `errors` |
| Lifetime | the thread | one `invoke()` |
| Travels via | LangGraph channels | `RunnableConfig["configurable"]` |
| Persisted | yes — small, serde-safe | never |

The API creates the carrier, passes it in the config, and reads the rich artifacts off it
after `invoke()` returns. `run_turn()` owns that lifecycle so callers cannot forget it.

**Rejected alternatives.** *Full rich state + stream capture* was the most idiomatic
LangGraph shape and gave Phase 6's SSE pane a free event source — but it persists the trace
into the checkpoint, violating both reasons above. *A single "thin shell" node* with the whole
pipeline as plain Python inside kept the checkpoint clean, but collapsed the graph: routing,
per-node observability, and the SSE streaming surface all disappear, leaving LangGraph as a
checkpoint wrapper. Neither trade was worth it.

## The finding that changed the enforcement design

The original plan assumed LangGraph would **reject** a node update targeting an undeclared
channel, giving a loud error for free. Verified on **langgraph 1.2.4** — it does not:

```
node returns {"answer": "ok", "undeclared": <object>}
RESULT: no raise; final state keys = ['a']    # 'undeclared' silently dropped
```

No exception, no warning. The value simply vanishes.

**This is safe but hostile.** Safe, because a dropped value never enters state and so never
reaches the checkpoint — the invariant survives on its own. Hostile, because a developer who
writes `return {"context": ctx}` gets *no signal at all* and debugs a silent disappearance
instead of reading an error that names the rule. That is a worse failure mode than the loud
one the design was written to provide.

The response was **not** to accept the weaker behavior, but to supply the missing signal
ourselves. A pinning test now records LangGraph's actual semantics, so if upstream ever
changes — especially to *persist* unknown channels — we learn immediately rather than at a
boundary violation.

## Three layers, and why only one of them is the guarantee

| Layer | Mechanism | What it is |
|---|---|---|
| **L1 — signal** | `_checked(name, node)` wraps every node and raises `RuntimeError` if its returned keys leave the `GraphState` allowlist | Developer feedback. Catches the slip early with a teaching error. **Not** a guarantee — it only sees node *outputs*. |
| **L2 — guarantee** | `_GuardedSerde` wraps the checkpointer's serde and raises `TypeError` when a banned type reaches `dumps_typed` | **The architectural enforcement point.** Every persist — production and test — flows through it. |
| **L3a — tripwire** | A test asserts `GraphState.__annotations__` equals an explicit allowlist; a second runs a real turn and inspects the persisted checkpoint | Makes adding a channel a conscious, reviewed act. |

The division matters. **L1 and L3a can be satisfied while the invariant is violated** — L1
sees only what nodes return, and L3a is a test, not a runtime property. L2 cannot: it sits on
the one path every write must take, so *adding a channel to `GraphState` does not sneak an
object past the boundary — it just makes the guard fire instead.* That is the difference
between "please don't checkpoint heavy objects" and "heavy objects cannot be checkpointed",
and it is the same invariant-by-construction posture as the registry drift canary, the
scoping security test, and ADR-12's trace-by-construction.

## Why the guard lives on the serde

Two placements were verified before implementation; both could see live Python objects, so
neither was blocked. The serde wrapper won because:

- It covers **`put()` and `put_writes()` in one place** — the pending-writes path is a second
  persist route that a `put()` override would miss.
- It is tied to **the act of serialization** rather than one method's argument shape, so it
  survives upstream refactors of `put()`'s internals.
- Installing it in the shared `_CockroachReads` mixin guards the **sync and async** savers by
  construction, including via `from_conn_string`.

The sweep covers the value itself plus one level into `list`/`tuple`/`set`/`dict`, which is
where an accidental `{"trace": [trace]}` channel would otherwise hide.

## Verified behavior (langgraph 1.2.4 / langgraph-checkpoint 4.1.1 / -postgres 3.1.0)

- Values reach `dumps_typed` as **live Python objects**; containers arrive as live `list`/`dict`.
- A raise inside the serde **prevents the write entirely** — `get_tuple()` returns `None`
  afterwards; nothing is half-persisted.
- Plain strings are inlined into the checkpoint row and do not pass through `dumps_typed`.
  Irrelevant here (every banned type is a dataclass), but worth knowing before assuming the
  guard sees *everything*.

## Maintenance notes

- **Do not weaken L2 to a warning or a log line.** If a future LangGraph version serializes
  channel values before any overridable seam sees them, that is a stop-and-review trigger, not
  an invitation to downgrade the guarantee to convention.
- **Adding a `GraphState` channel** is legitimate — but it must be small and serde-safe, and
  it must be added to the L3a allowlist deliberately, in the same change.
- **When T7 lands**, the trace gets a durable home in `evidence_traces`. That does not relax
  this boundary; it is the reason the boundary exists.
- **Honest scope.** No Python mechanism makes this physically impossible: a developer could
  add the channel (past L1), delete the guard (past L2), and update the allowlist (past L3a).
  The guarantee is that the accident cannot land *silently* and the override cannot land
  *invisibly* — it becomes a loud, self-explaining, three-part change touching a guard named
  after the ADR it enforces, where code review is the intended backstop. Adopting a type
  checker in CI (currently ruff only) would add a compile-time layer.

## Related files

| File | Role |
|---|---|
| `agent/checkpointer.py` | `_GuardedSerde` (L2), installed via `_CockroachReads.__init__` |
| `agent/graph.py` | `GraphState` (L1 allowlist), `TurnCarrier`, `_checked`, `run_turn` |
| `agent/tests/test_checkpoint_guard.py` | L2 tests against a real database |
| `agent/tests/test_graph_routing.py` | L3a allowlist tripwire + the LangGraph silent-drop pinning test |
