# CockroachDB: Lessons Learned Building a Memory Engine

> Engineering deep dive (see [README.md](README.md) conventions). A permanent record of the
> CockroachDB-specific problems this project actually hit, how each was diagnosed, and what the
> resolution was. Written so a future maintainer — human or AI — can tell a deliberate choice
> from an accident, and so the project can answer *"what engineering problems did you solve?"*
> with evidence rather than adjectives.
>
> Every measurement here was taken from this project's own cluster and test suite. Where a first
> diagnosis turned out to be wrong, the wrong turn is kept in the record: the reasoning that
> produced it is the reusable part.

---

## Part I — The incident: a 21½-minute DELETE

### 1. The problem as it appeared

Phase 5 added consolidation — the layer that derives insights from memories. As it landed, the
test suite started failing in a way it never had before:

```
psycopg.errors.SerializationFailure: restart transaction:
TransactionRetryWithProtoRefreshError: TransactionRetryError: retry txn (RETRY_SERIALIZABLE)
```

The pattern was maddening:

| Observation | |
|---|---|
| Failures per full run | 1–2, out of ~718 tests |
| Same test twice? | **Never** |
| Reproduces in isolation? | **Never** — the same test passed in 15 s on its own |
| Where it surfaced | Always at `COMMIT` |
| Suite runtime | Degraded from ~6 min to 8 min to **36 min** |

Three consecutive full-suite runs failed on three different tests. Each of those tests, run
alone, passed immediately. The affected files were often ones untouched for weeks.

### 2. The first diagnosis — and why it was wrong

The reasoning went: *Phase 5 multiplied the number of transactions. One consolidation pass opens
~6 transactions per series, and a sweep does that across 9 series. More transactions, more
contention, more retryable errors. And `Database.transaction()` has no retry logic, which
CockroachDB's documentation says clients must implement. Therefore: add retry.*

Every clause of that is true. The conclusion still did not follow.

**Why it was seductive.** The evidence fit. Failures appeared exactly when Phase 5's write
volume did. They moved between tests, which is what contention looks like. They surfaced at
COMMIT, which is where `RETRY_SERIALIZABLE` surfaces. And the fix was flattering — a known gap
against documented CockroachDB guidance.

**What was missing.** Every one of those observations is *equally* consistent with a different
story: not "many transactions" but **"one enormous transaction."** Nothing had been measured.
The hypothesis was reached by pattern-matching on the error name.

**A second error, worth recording.** The proposed fix was described as "wrap the body of
`Database.transaction()` in a retry loop — ~10 lines, no caller changes." That is not
implementable, and finding out why is instructive:

```python
@contextmanager
def transaction(self):
    with self.connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                yield cur          # ← control leaves for the caller's block

# caller
with db.transaction() as cur:
    cur.execute(...)               # ← this is what needs re-running
```

A context manager can *catch* an exception thrown in at the `yield`. It cannot **re-execute the
caller's block**, which lives in a frame it has no access to. Retrying a transaction means
re-running its statements. Any real retry support therefore has to change the *shape* of the
seam — a callable-taking `run(work)` — not just its body. Two wrong answers came out of the same
habit: asserting a mechanism without checking it.

### 3. The investigation

What broke the deadlock was refusing to reason further from the error message and instead
observing the running system.

**Step 1 — separate the code from the environment.** Run the suspect tests alone.

```
M5d file, isolated:     21/21 green, 2m18s   (×3 runs)
Errored test, isolated: passes, 15s
```

Clean in isolation, broken in aggregate. That excludes a logic defect and points at something
about the *whole run*.

**Step 2 — notice the runtime, not just the failures.** 36 minutes for a suite that took 6.
Failures explain themselves; a 6× slowdown does not. Something was consuming enormous wall
clock, and nothing in the test list should.

**Step 3 — watch the database instead of the test output.** The suite appeared hung at ~99 %.
Sampling row counts showed no activity. Rather than guess:

```sql
SELECT query, start FROM [SHOW CLUSTER STATEMENTS];
```

```
age = 1293.3s   DELETE FROM memories WHERE user_id = ANY ('{0044ea806a4546ef87c8cb01e0…
```

**21½ minutes, one statement, still running.** Not a test. The suite had finished testing and
was stuck in teardown.

**Step 4 — read the offending code.** It was ours, from an earlier milestone:

```python
cur.execute(f"DELETE FROM {table} WHERE user_id = ANY(%s)", [user_ids])
```

One statement, one array, **every user id the run had minted** — roughly 200 by then, and
growing with every test added to the suite.

### 4. Root cause

A single `DELETE ... WHERE user_id = ANY(<200 uuids>)` against a cross-region cluster
(app in `us-east-1`, database in `ap-south-1`) is a long-running **write** transaction touching
a large, scattered key range.

CockroachDB uses serializable isolation. A transaction that stays open long enough will have its
timestamp **pushed** — by contending traffic, by the closed-timestamp subsystem, by background
work. When a pushed transaction reaches `COMMIT`, it must *refresh* its read set to prove
nothing it read has changed under it. Over a 20-minute window across hundreds of ranges, that
refresh fails. The result is exactly what the suite kept reporting:
`RETRY_SERIALIZABLE`, surfaced at COMMIT.

**So the teardown was not a victim of the contention — it was the primary source of it.** It had
quietly become the longest and most contentious transaction in the run, and it grew every time
the suite did. Phase 5 did not cause the flake; Phase 5 grew the suite past the point where an
already-unbounded teardown became pathological.

### 5. The fix

Bound the statement. `engine/tests/dbcleanup.py` now deletes in batches:

```python
PURGE_BATCH = 50

def _delete_batched(cur, sql, values):
    total = 0
    for start in range(0, len(values), PURGE_BATCH):
        cur.execute(sql, [values[start : start + PURGE_BATCH]])
        total += cur.rowcount
    return total
```

The connection is `autocommit`, so each batch is its own implicit transaction. No teardown
statement can hold locks while the others run, and the parameter array is a fixed size no matter
how large the suite grows.

### 6. Before and after

Every full-suite run of this investigation, in order:

| # | Code | Result | Runtime | Teardown |
|---|---|---|---|---|
| A | pre-fix | 2 failed, 715 passed | 35 m 45 s | completed |
| B | pre-fix (after bounding a runaway test) | 1 error, 717 passed | 8 m 39 s | completed |
| C | pre-fix, re-sample | **killed** — stuck 1,293 s on one DELETE | — | never completed |
| D | **post-fix** | **718 passed, 0 failed, 0 errors** | 9 m 28 s | completed, 0 residue |

### 7. Verification — and what it does and does not prove

**Run D was fully green: 718 passed, zero failures, zero errors, in 9 m 28 s.** Teardown
completed and left `memories`, `users` and `sessions` at exactly **0**, with no long-running
statements on the cluster afterwards.

Three things are now established:

1. **Teardown always finishes.** Run C never did — it was killed after 21½ minutes and left
   1,396 orphan memories. Run D's batched teardown completed and cleaned up fully.
2. **The suite no longer degrades as it grows.** The unbounded statement grew with every test
   added; the batched one does not.
3. **No `SerializationFailure` occurred.**

**What this does *not* establish — stated plainly, because the temptation is to claim more.**
One clean run is a single sample against a defect that appeared 1–2 times per ~718 tests. A
flake at that rate can be absent from one run by chance. Two of two *completed* pre-fix runs
showed it and one of one post-fix run did not, which is **suggestive, not conclusive**. Honest
confidence: the teardown was a major contention source and very likely the dominant one; it has
not been proven the only one.

**The runtime is the most interesting number, precisely because it did not improve.** Run D
(9 m 28 s) is *slower* than run B (8 m 39 s). If the 21-minute DELETE had been a constant tax,
removing it would have shown up as a large, obvious win. It did not — which says the pathological
teardown was **intermittent**, appearing only when the accumulated id set and cluster state
lined up badly. That fits the evidence better than a simple "teardown was always slow" story,
and it is the reason this section resists reporting a speedup that is not there.

The right follow-up, if the flake ever returns, is not to assume this fix failed but to run
`SHOW CLUSTER STATEMENTS` again and find out *which* statement is long now — the one technique
in this whole investigation that produced an answer instead of a hypothesis.

### 8. Why retry support is still worth building

The oversized teardown was *this* bug. It is not the *only* reason a client meets
`RETRY_SERIALIZABLE`.

A deployed multi-user app meets genuine contention that no amount of transaction hygiene
removes: two users' turns touching adjacent rows, an analytical read racing live writes, a
retried HTTP request arriving twice. CockroachDB classifies these errors as **retryable**
precisely because the correct response is to run the transaction again — and its documentation
is explicit that clients must implement that.

Today `engine/db.py` propagates them, so genuine contention surfaces as a failed request rather
than a transparent retry. That is a real production-readiness gap, tracked in
[TODOS.md](../../TODOS.md), with the important caveat recorded above: the fix must add a
`run(work)`-shaped seam, because a context manager cannot retry its caller's block.

**The lesson is not "retry was the wrong idea."** It is that retry would have *masked* this bug
— turning a 21-minute transaction into three 21-minute transactions — while the real defect,
an unbounded statement, went on growing.

---

## Part II — Everything else CockroachDB taught us

Each item below was hit while building this project, in the order it was encountered.

### 9. Vector indexing: the index is real, but filters make the planner abandon it

**What we needed.** Semantic recall over `VECTOR(512)` embeddings, scoped to one user.

**What the day-one canary proved (2026-07-17).** A `VECTOR(512)` column with a C-SPANN index
works on the affordable tier, K-NN ordering on normalized vectors is correct, and `EXPLAIN`
shows the plan using the index.

**What we then discovered.** The canary's query was *unfiltered*. The product's query is not — it
filters `user_id`, `status = 'active'`, and `embedding IS NOT NULL`. With any of those present,
the planner falls back to a scan plus a top-k sort. A follow-up probe on a scratch table tested
whether a *prefixed* vector index would help:

| Query shape | Uses the vector index? |
|---|---|
| Pure K-NN, no filter | ✅ |
| Prefix + `status = 'active'` | ❌ scan |
| Prefix + `embedding IS NOT NULL` | ❌ scan |
| Prefix + both | ❌ scan |

**Decision: keep the filters, accept the scan.** Correctness first — a recall that leaked another
user's memories, or surfaced a superseded row, would be a far worse defect than a slow query at
demo scale. The honest framing is written into the README rather than hidden: the index is the
right structure for lifelong, multi-user scale; at one user's scale the scan is faster anyway.

**Transferable lesson:** *benchmark the query your product runs, not the query the tutorial runs.*
A canary that proves a feature works can still prove nothing about your access pattern.

### 10. Distance metric: C-SPANN is Euclidean-only

CockroachDB's vector index supports L2 distance, not cosine. Health-memory recall wants cosine
similarity.

**Resolution:** normalize every embedding to unit length. On unit vectors, L2 distance and cosine
similarity are monotonically related, so ordering by `<->` gives cosine ranking. Titan Text
Embeddings V2 supports `normalize=true` at the source, so this costs nothing.

The invariant is non-obvious and load-bearing, so it is guarded by a permanent canary test and
carries a TODO explaining when it can be deleted (when CockroachDB ships cosine distance).
*A workaround nobody documented is indistinguishable from a bug.*

### 11. Batch inserts and the C-SPANN footgun

Bulk-inserting rows with vector values degrades badly. The replay CLI — which pushes 424 records
through the production write path — inserts **row-at-a-time by design**, one record per
transaction.

The cost is real (~1.01 s/record, no parallelism) and was accepted deliberately: at 424 records
it is irrelevant, and it kept the write path's guarantees a single testable property rather than
two. This is written into the architecture as a rule with a reason attached, because "why is this
loop not batched?" is exactly the optimization a future contributor would helpfully introduce.

### 12. LangGraph's PostgresSaver does not run on CockroachDB

Day-one canary #2 (same gate class as the vector canary, deliberately) tested whether LangGraph's
stock `PostgresSaver` works. **It does not** — and the failure is in the SQL dialect corners, not
the feature set:

1. **Unaliased set-returning function references.** PostgreSQL lets you reference an SRF's output
   columns in ways CockroachDB rejects.
2. **Two-dimensional `bytea` arrays.** The upstream read query builds
   `array_agg(array[bl.channel::bytea, bl.type::bytea, bl.blob])` — a 2-D `bytea` array.
   CockroachDB does not support multidimensional arrays
   ([cockroachdb#32552](https://go.crdb.dev/issue-v/32552/)), and the
   rejection happens at **plan/describe time**, so it fails even against zero rows.

**Resolution:** a thin subclass, `CockroachDBSaver`, rewriting *only* the read query to use
`jsonb_agg(jsonb_build_array(...))` with base64-encoded blobs, plus two loader overrides.
`.setup()` migrations and every write path run unmodified.

The pre-agreed fallback landed far smaller than feared, which is the point of running the canary
on day one: *the two riskiest external bets were tested in week one, when a bad answer was still
cheap.* Full investigation: [cockroachdb-postgressaver.md](cockroachdb-postgressaver.md).

### 13. `schema_locked` blocks `TRUNCATE`, and the client hangs instead of erroring

CockroachDB v25+ sets `schema_locked = true` on tables by default (a changefeed optimization).
`TRUNCATE` is a schema change and is refused.

**The dangerous part is the failure mode.** The web SQL console shows a clean error immediately.
The psycopg client **hangs and then drops the connection** — which looks like throttling, a
network problem, or a deadlock. We lost time chasing all three.

**Working recipe:**

```sql
ALTER TABLE <t> SET (schema_locked = false);   -- one statement per implicit transaction
TRUNCATE <t>;
ALTER TABLE <t> SET (schema_locked = true);
```

Batched statements are rejected. `DELETE` is DML and is never blocked, which is why every
cleanup path in this project uses `DELETE`.

### 14. Test-data accumulation will kill a cluster, silently

The most expensive lesson of the project, and it took two separate incidents to learn.

**Incident one (Phase 4).** Test fixtures minted a fresh `uuid4()` user per test and nothing ever
deleted the rows. `memories` has **no foreign key to `users`**, so the orphans were not even
cascade-deletable. The cluster reached **8,996 memories across 4,690 users** and the full suite
stopped passing entirely — ~26 consecutive attempts, never clean, always
`SerializationFailure`, never an assertion failure.

The decisive experiment: replace the cluster, change no code. The identical suite passed
**445/445 on the first run**. That proved the failures tracked *cluster state*, not code — and it
is the cheapest diagnostic available when a suite is failing non-deterministically.

**Incident two (Phase 5).** The fix for incident one — a session-scoped purge — became the
21-minute DELETE of Part I. *The cleanup we added to solve the accumulation problem became the
next problem.*

**What we ended with:** a registry of ids the run mints, purged in bounded batches at session
end. The registry approach matters as much as the batching: it deletes **only ids this process
created**, so it cannot touch a row it did not make — a safety property that holds even if
`DATABASE_URL` is misconfigured.

**Transferable lesson:** *a test suite that writes to a shared database needs a cleanup story
from day one*, and that story needs a bound. Both halves are load-bearing.

### 15. Never let a test invoke a cluster-wide sweep

Two separate tests in this project called an operator command that iterates **every account in
the database**. Correct for the command; catastrophic for a test.

Measured: during a full run, one such test swept ~150 accumulated accounts × 9 series ≈ **14
minutes inside a single test**, and took the suite from 8 to 36 minutes.

**Resolution:** stub discovery and test the *loop*; give discovery its own single-query test.
Coverage went up, not down. This pattern bit us twice (`test_backfill.py`, then
`test_consolidate.py`), which is why it is now written down as a rule rather than a war story.

### 16. A JSON `null` is not a SQL `NULL`

Filtering optional JSONB objects with `IS NOT NULL` is wrong:

```sql
-- WRONG: a JSON null passes this
WHERE payload -> 'retraction_condition' IS NOT NULL

-- RIGHT
WHERE jsonb_typeof(payload -> 'retraction_condition') = 'object'
```

A payload written by our own serializer omits absent keys entirely, so both spellings agreed and
the bug was invisible. A hand-repaired row containing `"retraction_condition": null` is exactly
where they diverge. Found by writing the test before trusting the query.

### 17. Cross-region latency dominates everything

The app runs in `us-east-1`; the database is in `ap-south-1`. One round trip is ~200–250 ms.

**Measured consequence:** a single consolidation series costs **~635 ms**, and a 9-series pass
~5.7 s. An architectural decision made months earlier budgeted ~300 ms for in-request
consolidation — which, on this topology, completes **exactly one series** before deferring the
rest.

The mechanism behaves correctly (clean deferral, nothing partial), but the *number* was written
before the topology existed. It is now flagged as provisional in the ADR itself, pending a
measurement task against the deployed service.

**Transferable lesson:** *a latency budget written before the deployment topology exists is a
guess wearing a number's clothing.* Mark it provisional, and re-derive it from measurement.

### 18. Smaller things worth knowing

| Quirk | What we do |
|---|---|
| Alias resolution in `GROUP BY` | Use the ordinal (`GROUP BY 1`) to sidestep dialect differences |
| Timezone-correct day bucketing | `date_trunc(%(period)s, event_time AT TIME ZONE %(tz)s)` — the zone is a **bound parameter**, never interpolated |
| Citable aggregates | `array_agg(id ORDER BY event_time, id)` returns contributing row ids alongside the computed number, so a total stays clickable |
| `DISTINCT ON` | Supported, but we reduce in Python where the reduction is part of a *definition* — keeps it unit-testable without a database |
| Long-lived connections | The LangGraph checkpointer holds one connection for the app's lifetime; CockroachDB Cloud closed it after ~10 h idle, and an uncaught `OperationalError` became a 500. Now mapped to a 503 |
| `IPv6` first-connect | `psycopg` tries `::1` before `127.0.0.1`; on Windows against an IPv4-only node that costs ~130 s. Connection strings use `127.0.0.1` explicitly |
| Inverted JSONB indexes | What make the migration-free payload model queryable — a new nutrient is a new key, never a migration |

---

## Part III — Hackathon Experience Using CockroachDB

*Written for Devpost, judge Q&A, and talks. Honest about the friction, because the friction is
the interesting part.*

### Why CockroachDB was load-bearing, not a checkbox

This project's thesis is that health memory is mostly **typed quantitative events**, and that the
questions people actually ask are *computations*: "show my protein during June" is
`SUM(payload->>'protein_g') … GROUP BY week`, not a similarity search. No vector store can
compute it.

So the engine needed **SQL aggregation and vector search over the same rows, in one
transactionally consistent store**. That requirement is what selected CockroachDB, and it is why
swapping it out would not be a port — it would be a redesign. One store means an ingested meal is
immediately visible to *both* the aggregation path and the semantic path, with no window where
the agent's "computed" and "remembered" views of the user disagree. That consistency property is
the argument judges can verify in the glass box, because the UI shows the actual executed
queries.

### What went well

- **`VECTOR(512)` + JSONB + relational, in one table.** The two-tier memory model — episodic
  events and derived insights — is one `memories` table with a typed JSONB payload and a nullable
  vector column. No second datastore, no sync gap, no dual-write problem.
- **Inverted JSONB indexes made the migration-free design real.** "A new nutrient tomorrow is a
  new key, not a migration" is a design rule we actually kept for the whole project.
- **Postgres wire compatibility meant the ecosystem mostly just worked** — psycopg, standard
  tooling, familiar SQL. The exceptions were narrow and findable (§12).
- **Serializable isolation by default.** Every bug in this document is a bug CockroachDB
  *told us about*. A database with weaker defaults would have let the same access patterns
  through silently and produced wrong data instead of loud errors.

### What was hard, and what we would tell the next team

1. **Test your query, not the tutorial's.** Our vector index worked perfectly in the canary and
   is bypassed by the product's own filtered query. We found that because we ran `EXPLAIN` on the
   real thing. (§9)
2. **Run the risky compatibility bets on day one.** Two canaries — vector indexing and LangGraph
   checkpointing — were written before any feature code. One failed. Discovering that in week one
   cost a thin subclass; discovering it in week five would have cost the architecture. (§12)
3. **Give your test suite a cleanup story before you need one.** Ours took down a cluster, and
   the fix for that took down a test run. Both were avoidable with a bound. (§14, Part I)
4. **When a suite fails non-deterministically, suspect state before code.** Replacing the cluster
   and changing nothing else turned 26 consecutive failures into 445/445 — a five-minute
   experiment that settled a question days of code review had not. (§14)
5. **Read `SHOW CLUSTER STATEMENTS` before theorising.** One query found a 21-minute DELETE that
   two rounds of plausible reasoning had missed entirely. (Part I §3)
6. **A retryable error is a symptom, not a diagnosis.** `RETRY_SERIALIZABLE` says *something* is
   contending. It does not say what. Adding retry without finding out would have hidden the real
   defect while it kept growing. (Part I §8)
7. **Read the deploy action's source, not its README.** Our ECS task definition had no
   environment variables, and the interesting part was *why they would not have survived if we
   had added them by hand*: `amazon-ecs-deploy-express-service@v1` rebuilds the container
   config from its inputs on every run and never reads the live service back, so console
   configuration silently evaporates on the next deploy — and the same applies to the task role
   and health-check path. The README does not document update semantics; 700 lines of
   `index.js` do. Configuration is now declared in the workflow, secrets come from Secrets
   Manager, and a preflight step fails the job rather than shipping a half-configured service.
   Full write-up in [deploy.md](../deploy.md) → *Runtime configuration (declarative)*. The
   failure mode this prevents is the CockroachDB-flavoured one: an unset `DATABASE_URL`
   silently falls back to `127.0.0.1`, and because the app tolerates a dead database at
   startup so the ALB check stays green, **`/healthz` reports healthy while every write
   fails**. A green health check is not evidence of a working database.

### The honest summary

We hit real friction: a vector index that our own filters bypass, a LangGraph integration that
needed a compatibility shim, `TRUNCATE` silently blocked by a v25 default, a cluster killed by
test accumulation, and a self-inflicted 21-minute transaction. None of it was CockroachDB
behaving badly — every one was either a documented constraint we had not measured against, or our
own code meeting a stricter database than it was written for.

What we would say to a judge asking *"what did you actually learn?"*: the database's strictness
was a feature. Serializable isolation turned three separate design mistakes into loud, findable
failures instead of quiet data corruption. The work was in learning to read what it was telling
us — and in resisting the plausible fix long enough to find the real cause.

---

## Related files

| File | Relationship |
|---|---|
| `engine/tests/dbcleanup.py` | The batched purge (Part I §5) |
| `engine/db.py` | The transaction seam; retry support tracked in TODOS (§8) |
| `engine/repository.py`, `engine/retrieval.py` | Row-at-a-time inserts, parameterized builders, the SQL quirks of §18 |
| [vector-index-and-filtered-knn.md](vector-index-and-filtered-knn.md) | The full vector-index measurement (§9) |
| [cockroachdb-postgressaver.md](cockroachdb-postgressaver.md) | The LangGraph compatibility investigation (§12) |
| [replay-architecture.md](replay-architecture.md) | Row-at-a-time batching rule (§11) |
| [consolidation-architecture.md](consolidation-architecture.md) | Cross-region measurements, the sweep rule (§15, §17) |
| [../deploy.md](../deploy.md) | Declarative ECS runtime configuration; why console-set env vars do not survive a redeploy (Part III §7) |
| [../../TODOS.md](../../TODOS.md) | Retry support; the corrected root-cause record; the M6/T12 postponement and its resume point |
