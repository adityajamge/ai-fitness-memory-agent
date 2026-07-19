# LangGraph Checkpointing on CockroachDB — Engineering Deep Dive

> **Audience:** future maintainers (human or AI) who need to understand why
> [`agent/checkpointer.py`](../../agent/checkpointer.py) exists, how the compatibility layer
> works, and how to modify it safely. This is not an ADR (the decision record is
> [09-decisions.md → ADR-13.8](../office-hours/09-decisions.md)) and not a user guide — it is
> the engineering reasoning behind the code, written down while it was fresh (investigation:
> 2026-07-17, langgraph-checkpoint-postgres 3.1.0, CockroachDB v26.2.4).
>
> **This document is the canonical implementation reference for the CockroachDB/LangGraph
> checkpoint compatibility layer.** ADRs, tasks, and implementation notes that touch
> `CockroachDBSaver`, `PostgresSaver`, or the checkpoint canary should link here rather than
> re-explain any of it.

---

## 1. Background

### What LangGraph is, and what state it needs

Our agent ([05-agent-architecture.md](../office-hours/05-agent-architecture.md)) is a
LangGraph graph: a set of nodes (intent routing, planning, engine tool calls, narration,
trace assembly) connected by edges, executed step by step for every conversation turn.
LangGraph is a *stateful* orchestrator — between node executions it carries **graph
execution state**: the message history the graph has accumulated, intermediate channel
values, which node ran last, what a pending tool call was waiting for.

### Why checkpointing exists

Without persistence, that state lives in process memory and dies with the process. A
**checkpointer** writes a snapshot of graph state after each step, keyed by `thread_id`
(one thread ≈ one conversation). That buys three things:

1. **Multi-turn conversations across requests.** Each HTTP request is a fresh graph
   invocation; the checkpointer is how turn *n+1* sees turns *1..n*.
2. **Resume after crash or deploy.** The host (ECS) restarts the container; conversations
   continue where they left off.
3. **Replayability/time-travel of graph execution** (LangGraph can resume from any stored
   checkpoint id) — not a feature we surface, but it shapes the storage model below.

### Execution state is NOT application memory

This distinction is load-bearing in this project (ADR-13.14). The checkpointer stores
**graph plumbing** — LangGraph's internal channels. The *product* — typed memories,
insights, evidence traces, turns — lives in the app's own tables, written by the Memory
Engine, and is the only source of truth for UI rendering. If every checkpoint row vanished,
users would lose in-flight conversation context but no memories, no insights, no traces.
Never read checkpoint tables to render UI; never write app data through the checkpointer.

### What PostgresSaver actually is

`PostgresSaver` (package `langgraph-checkpoint-postgres`) is LangChain's official
production checkpointer: a class implementing LangGraph's `BaseCheckpointSaver` interface
(`put`, `put_writes`, `get_tuple`, `list`, `delete_thread`, plus `setup()` for DDL
migrations) against a PostgreSQL database via psycopg 3. It is ordinary, readable code —
a handful of SQL statements and (de)serialization helpers — which is precisely why a thin
subclass was a viable fallback.

## 2. Checkpoint Architecture

```
User
  │  HTTP request (one conversation turn)
  ▼
LangGraph Agent          ── nodes execute: route → plan → engine tools → narrate
  │
  ▼
Graph State              ── channels: messages, intermediate values, versions
  │  after each step
  ▼
PostgresSaver            ── serializes channels, writes checkpoint keyed by thread_id
  │  (here: CockroachDBSaver)
  ▼
CockroachDB              ── same cluster as memories/turns/traces, separate tables
```

Lifecycle of a thread:

- A conversation gets a `thread_id`. Every graph invocation for that conversation passes
  `{"configurable": {"thread_id": ...}}`.
- After each superstep LangGraph calls `put(config, checkpoint, metadata, new_versions)`.
  A **checkpoint** is a dict (current format `"v": 4`) holding `channel_values` (the
  state), `channel_versions` (a monotonically-bumped version per channel), and bookkeeping
  (`id`, `ts`, `versions_seen`). Checkpoint ids sort lexicographically in creation order,
  so "latest checkpoint" is `ORDER BY checkpoint_id DESC LIMIT 1`.
- On the next invocation LangGraph calls `get_tuple(config)` to load the latest checkpoint
  and resumes from it. `list()` returns a thread's history (newest first).
- `delete_thread(thread_id)` removes all rows for a conversation.

**Key invariant discovered during T2** (it bit the first canary draft): channel *versions*
identify channel *values*. A changed value ⇒ a bumped version. Storage exploits this by
treating a `(channel, version)` blob as immutable (`ON CONFLICT DO NOTHING`) — writing a
different value under an existing version is silently ignored, by design.

## 3. Storage Schema

`setup()` creates (and migrates, via a `checkpoint_migrations` version table) three data
tables. They exist to avoid rewriting large state on every step:

| Table | One row per | Stores | Why it exists |
|---|---|---|---|
| `checkpoints` | (thread, ns, checkpoint) | the checkpoint JSONB — metadata, `channel_versions`, and **primitive** channel values inlined | the spine; small, written every step |
| `checkpoint_blobs` | (thread, ns, channel, **version**) | serialized **non-primitive** channel values (bytea) | large values written only when their channel actually changes; immutable per version |
| `checkpoint_writes` | (thread, ns, checkpoint, task, idx) | pending writes from tasks that completed while the step was still in flight | crash-safety for partially-completed supersteps |

The split explains the read query's shape: to reconstruct full state, `get_tuple` must
join `checkpoints.channel_versions` (a JSONB map like `{"messages": "3"}`) against
`checkpoint_blobs` on `(channel, version)` — each checkpoint references the *specific
version* of each channel's blob that was current when it was taken. That join is exactly
where CockroachDB compatibility broke.

A subtlety that cost us an hour (see §6): `put()` inlines primitive channel values
(str/int/float/bool/None) into the checkpoint JSONB and writes **only non-primitives** to
`checkpoint_blobs`. A test that uses only string values never touches the blob table and
therefore never exercises the join.

## 4. Why CockroachDB (and not a side-Postgres)

The obvious question: PostgresSaver is built for PostgreSQL, so why not give checkpoints a
small PostgreSQL instance and skip this entire document?

Because the project's storage thesis (ADR-3, ADR-13) is **one transactionally consistent
store**. CockroachDB already holds typed JSONB memories, `VECTOR(512)` embeddings, users,
turns, and evidence traces in a single cluster. Adding a second database engine for one
component would mean:

- a second connection string, second CI service container, second thing to provision,
  secure, and pay for — for a solo hackathon builder, pure overhead;
- a demo story that undercuts itself ("CockroachDB is the system of record… except over
  here");
- cross-store consistency questions we otherwise don't have.

CockroachDB speaks the Postgres wire protocol and most of its SQL dialect, so the *bet*
(ADR-13.8, assumption 5) was that PostgresSaver would run unmodified. The engineering
review flagged this as an unverified compatibility bet (outside voice #10) — CockroachDB
is wire-compatible, **not** Postgres — and required a day-one canary with a pre-agreed
fallback: "a thin hand-rolled checkpointer if the canary fails."

Trade-off accepted with eyes open: CockroachDB's SQL surface has gaps (its docs are honest
about them), so anything that ships Postgres-flavored SQL — like PostgresSaver — may need
adaptation. That is exactly what happened, and the cost was ~60 lines.

## 5. The Canary Test (T2)

[`agent/tests/test_checkpointer_canary.py`](../../agent/tests/test_checkpointer_canary.py)
is a permanent CI test, not a one-off probe. It exercises the full API surface the agent
will use, in one flow:

1. `setup()` — runs the DDL migrations (the likeliest breakage point, we assumed —
   wrongly, as it turned out).
2. `put()` twice — with **both** kinds of channel values (a primitive string and a
   non-primitive list) so both the inline path and the `checkpoint_blobs` path are
   written, and with a version bump between puts (the immutability invariant from §2).
3. `get_tuple()` — latest checkpoint: correct id, both channel values round-tripped,
   metadata intact.
4. `list()` — history in `[newest, oldest]` order, older blob still correct.
5. `delete_thread()` — cleanup (the tables stay; they are the production tables).

Skip semantics matter: with no reachable database the test skips **visibly**; with `CI` or
`REQUIRE_DB` set it fails hard. A canary that silently skips in CI is worse than no canary.

Why this had to run before Phase 2: everything from Phase 3 onward assumes conversations
persist. Discovering in week 4 that the checkpointer can't read its own writes would have
forced either an emergency storage redesign or a hand-rolled checkpointer under deadline
pressure. Running it on day one cost half a day and converted an assumption into a
verified interface with a pinned regression test. The canary paid for itself immediately —
it failed.

## 6. Investigation Timeline

Chronological, with the reasoning at each step. Environment: Windows 11, Python 3.10,
psycopg 3.3.4, langgraph-checkpoint-postgres 3.1.0, local single-node CockroachDB v26.2.4
(native binary, in-memory store), later the real CockroachDB Cloud cluster.

**Initial assumption.** PostgresSaver works unmodified; the canary is a formality that
becomes a regression test. Expected failure point if any: `setup()` migrations (DDL is
where dialects usually diverge).

**Run 1 — stock `PostgresSaver`: `setup()` ✅, `put()` ✅ (×2), `get_tuple()` ❌.**

```
psycopg.errors.UndefinedTable: no data source matches prefix: jsonb_each_text in this context
```

The assumption inverted immediately: DDL and writes were fine; the *read* query broke.
Located the SQL: upstream `SELECT_SQL` (in `langgraph/checkpoint/postgres/base.py`) joins
the blobs table like this:

```sql
from jsonb_each_text(checkpoint -> 'channel_versions')
inner join checkpoint_blobs bl
    on ... and bl.channel = jsonb_each_text.key
           and bl.version = jsonb_each_text.value
```

PostgreSQL lets you reference a set-returning function's output columns via the
function-name prefix (`jsonb_each_text.key`). CockroachDB requires an alias. Verified the
diagnosis in raw SQL before touching Python: the original construct reproduces the error;
adding `as cv(key, value)` and using `cv.key` / `cv.value` planned and executed cleanly.

**Fix 1 — string-patch `SELECT_SQL` in a subclass** (alias the SRF). All upstream reads go
through `self.SELECT_SQL`, so a class attribute override covers `get_tuple` and `list`.

**Run 2 — SRF error gone; new failure, same call site:**

```
psycopg.errors.FeatureNotSupported: unimplemented: unsupported binary serialization
of multidimensional arrays  (cockroachdb issue #32552)
```

The query now *planned*, but the result couldn't be returned. The two aggregate columns
are built as `array_agg(array[bl.channel::bytea, bl.type::bytea, bl.blob])` — a
**two-dimensional `bytea` array**. Upstream's `_cursor()` hardcodes
`conn.cursor(binary=True, ...)` (a perf optimization on Postgres), and CockroachDB cannot
serialize multidimensional arrays in the binary wire format.

**Hypothesis: force text-format results.** Reading psycopg source showed
`cursor(binary=True)` merely assigns `cur.format = BINARY` — an ordinary attribute — so a
`_cursor()` override could flip it back to TEXT after construction. Cheap to try.

**Run 3 — with TEXT results, a third error, and a different kind:**

```
psycopg.errors.ArraySubscriptError: multidimensional arrays must have array
expressions with matching dimensions
```

This one came from *executing* the query, not serializing results. At this point the
correct conclusion was available but not yet drawn — instead, a detour:

**Detour (instructive).** A standalone probe of the rewritten query returned `NULL` for
both aggregates, and the blobs table turned out to be **empty**. Momentary panic —
had `put()` silently written nothing? No: reading `put()` showed the primitive-inlining
rule (§3). The canary's string-only channel values never produced blob rows. Two real
findings fell out of the detour:

- **Both multidim-array failures are structural, not data-dependent.** They fire with
  *zero rows*, at plan/describe time. `array_agg(array[...])` of `bytea` is simply not a
  shape CockroachDB can produce today, in either wire format. Patching cursor formats was
  treating a symptom; the column type itself had to go.
- **The canary was under-testing.** It needed a non-primitive channel value to exercise
  the join at all, and (discovered one run later) a version bump between puts, because
  blobs are immutable per `(channel, version)` — the first draft reused version 1 with a
  changed value and read back the *old* value, which is correct checkpointer behavior and
  a wrong test.

Also solved en route: every saver-connected run took ~135 s. Not the migrations —
`from_conn_string` passes no `connect_timeout`, psycopg resolves `localhost` to `::1`
first, and Windows spends ~130 s timing out the IPv6 connect against an IPv4-only local
node. Both canaries now default to `127.0.0.1`. (Irrelevant in CI/cloud where
`DATABASE_URL` is explicit — but a nasty local red herring, because the slowness looked
like part of the compatibility problem.)

**Fix 2 — design the real compatibility layer.** Requirements: eliminate the
multidimensional array column entirely; keep `_load_checkpoint_tuple` (the row consumer)
untouched; keep every write path untouched. The natural CockroachDB-native shape for
"list of tuples" is JSONB:

```sql
jsonb_agg(jsonb_build_array(bl.channel, bl.type, encode(bl.blob, 'base64')))
```

with the binary blob base64-encoded into the JSON. The probe confirmed CockroachDB plans
`jsonb_agg(... order by ...)` (needed for `pending_writes` ordering). On the Python side,
only the two tiny helpers that consume these columns — `_load_blobs` and `_load_writes` —
need overrides that base64-decode before handing bytes to the serializer. The `_cursor()`
format hack became unnecessary and was deleted: binary results are fine once no
multidimensional array exists.

**Run 4 — canary green locally in 0.33 s** (the 135 s runs were the IPv6 stall all along).

**Run 5 — canary green against the real CockroachDB Cloud cluster** (33.6 s including
`setup()` migrations creating the production checkpoint tables there). Outcome recorded in
ADR-13.8; T2 closed.

## 7. The Compatibility Layer

[`agent/checkpointer.py`](../../agent/checkpointer.py), ~60 lines including docs:

```python
class _CockroachReads:              # mixin: everything that differs
    SELECT_SQL = SELECT_SQL         # full rewrite of the read query (jsonb aggregates,
                                    # aliased SRF); same columns, same trailing space
    def _load_blobs(...): ...       # consume jsonb rows, base64-decode blobs
    def _load_writes(...): ...      # same, for pending writes

class CockroachDBSaver(_CockroachReads, PostgresSaver): ...
class AsyncCockroachDBSaver(_CockroachReads, AsyncPostgresSaver): ...
```

What is overridden, and why exactly this much:

- **`SELECT_SQL`** — the read query, rewritten with (a) the SRF aliased as
  `cv(key, value)` and (b) both aggregate columns built as `jsonb_agg(jsonb_build_array(...))`
  with `encode(blob, 'base64')`. Column names and order match upstream exactly, and the
  string ends with the same trailing `"from checkpoints "` (callers concatenate WHERE
  clauses directly — that space is load-bearing).
- **`_load_blobs` / `_load_writes`** — upstream expects lists of `bytes` tuples from the
  2-D arrays; ours receive JSONB-decoded lists of strings and base64-decode the blob
  before calling the same `self.serde.loads_typed(...)`. The `type_ != "empty"` filter is
  preserved.
- **Nothing else.** `setup()`, migrations, `put`, `put_writes`, `delete_thread`,
  `from_conn_string` (it constructs via `cls`, so subclassing keeps it), serialization,
  search/filter logic — all inherited byte-for-byte.

Why a thin subclass and not a fork (or a from-scratch checkpointer):

- **Divergence is the maintenance cost.** Every upstream line we *don't* copy is a line
  whose bug fixes and behavior changes we inherit for free on `pip install -U`. A fork
  freezes ~700 lines at their 3.1.0 behavior; the subclass freezes one query and two
  10-line helpers.
- **The failure surface was precisely the read path.** The canary proved everything else
  compatible; rewriting proven-working code is negative-value work.
- **Failures are loud.** If upstream renames result columns, `_load_checkpoint_tuple`
  raises `KeyError`; if it reshapes the row contract, the canary fails in CI. There is no
  configuration in which drift silently corrupts state.

(An earlier iteration patched upstream's `SELECT_SQL` via `str.replace` with import-time
asserts. The final version writes the query out in full instead: the jsonb rewrite changed
too much for surgical string replacement to stay readable, and the canary — not an
import-time assert — is the real drift detector.)

Known limitation, documented in the module docstring: checkpoints with format `v < 4`
would route through upstream's `SELECT_PENDING_SENDS_SQL`, which also builds
multidimensional arrays. This app has only ever written v4 checkpoints, so that path is
unreachable here. If you ever import pre-v4 threads from elsewhere, that query needs the
same jsonb treatment.

## 8. Lessons Learned

- **Risk-driven ordering works.** The two riskiest external bets (vector indexing,
  checkpointer compatibility) were tested before any feature code existed. One of the two
  failed. Finding this on day one made it a half-day task; finding it in week 4 would have
  been a crisis. Prove infrastructure before building on it.
- **A canary must be able to fail loudly.** Visible skip locally, hard fail in CI. And it
  must test the *real* data paths — the first draft, with only primitive values, would
  have passed against a blob path that was completely broken.
- **"Wire-compatible" is not "compatible."** CockroachDB speaks Postgres's protocol
  faithfully; the divergence lives in the SQL dialect corners (unaliased SRF references,
  multidimensional arrays) and even in wire-format details (binary multidim serialization).
  Assume nothing; verify with the actual client library, not just a SQL shell.
- **Distinguish structural failures from data failures early.** The multidim errors fired
  with zero rows. Realizing a failure is *shape*-level, not *content*-level, immediately
  rules out whole classes of fixes (cursor formats, data cleanup) and points at the query.
- **Patch the smallest surface that owns the problem.** The problem was one query and its
  two consumers; the fix is one query and its two consumers. Everything else is upstream's.
- **Verify diagnoses in the lowest layer available.** Every SQL hypothesis was confirmed
  in raw SQL (CLI or bare psycopg) before touching the Python. That kept "psycopg
  behavior," "upstream saver behavior," and "CockroachDB behavior" from blurring together —
  and it is how the 135 s red herring got correctly attributed to Windows IPv6 fallback
  instead of the database.
- **Read the upstream source; it's shorter than guessing.** The decisive facts —
  `self.SELECT_SQL` indirection, `cursor(binary=True)` being a plain attribute assignment,
  primitive-value inlining in `put()`, blob immutability — each came from a few minutes in
  site-packages, and each redirected the fix.

## 9. Future Maintenance Notes

**When this layer can be removed.** When stock `PostgresSaver` passes the canary. That
requires CockroachDB to support both (a) function-name-prefix references to unaliased SRFs
and (b) multidimensional `bytea` arrays end-to-end including binary serialization
([cockroachdb #32552](https://go.crdb.dev/issue-v/32552/)) — or upstream to drop those
constructs from its read SQL. To check: temporarily swap `CockroachDBSaver` for
`PostgresSaver` in the canary and run it. If it passes against both local and cloud,
delete `agent/checkpointer.py`, point the agent at stock `PostgresSaver`, and keep the
canary (it guards the next upgrade either way).

**After every `langgraph-checkpoint-postgres` upgrade:**

1. Run the canary (it runs in CI anyway): `pytest agent/tests/test_checkpointer_canary.py`.
2. Diff upstream's `base.py::SELECT_SQL` against ours — if upstream added/renamed result
   columns or changed `_load_checkpoint_tuple`'s expectations, mirror the change in our
   `SELECT_SQL` (keeping jsonb aggregates) and rerun.
3. Check that `_load_blobs` / `_load_writes` signatures still match upstream's call sites.

**How to rerun the canary locally** (no Docker required — a native binary works):

```bash
cockroach start-single-node --insecure --store=type=mem,size=1GiB \
    --listen-addr=localhost:26257
pytest agent/tests/test_checkpointer_canary.py            # skips if DB missing
REQUIRE_DB=1 pytest agent/tests/test_checkpointer_canary.py   # fails if DB missing
DATABASE_URL=<cluster-url> REQUIRE_DB=1 pytest agent/tests/test_checkpointer_canary.py
```

(Note the `127.0.0.1` default in the tests — see the IPv6 story in §6 before "fixing" it
back to `localhost`.)

**Do not modify casually:**

- The trailing space in `SELECT_SQL`'s `"from checkpoints "` — WHERE clauses are
  concatenated onto it.
- The result column names/order — `_load_checkpoint_tuple` consumes them by name.
- The `(channel, version)` join semantics or the `type_ != "empty"` filter — they encode
  upstream's blob-immutability contract (§2).
- The canary's version-bump between puts — it looks redundant and is not; see §6.
- Checkpoint tables in production — they are LangGraph's, not ours (ADR-13.14). App
  features must never read or write them directly.

## 10. Related Files

| File | Role |
|---|---|
| [`agent/checkpointer.py`](../../agent/checkpointer.py) | the compatibility layer itself (sync + async) |
| [`agent/tests/test_checkpointer_canary.py`](../../agent/tests/test_checkpointer_canary.py) | permanent canary/regression test (T2) |
| [`docs/office-hours/09-decisions.md`](../office-hours/09-decisions.md) | ADR-13.8 — the decision + recorded canary outcome |
| [`docs/office-hours/11-implementation-tasks.md`](../office-hours/11-implementation-tasks.md) | T2 backlog entry (closed 2026-07-17) |
| [`docs/office-hours/05-agent-architecture.md`](../office-hours/05-agent-architecture.md) | where the checkpointer sits in the agent design |
| [`engine/tests/test_vector_canary.py`](../../engine/tests/test_vector_canary.py) | sibling day-one canary (T1) — same skip/REQUIRE_DB pattern |
