# The Vector Index and Filtered K-NN

> Canonical reference for how `recall_memories` (`engine/retrieval.py`) actually executes on
> CockroachDB, and for the honest scale answer T17 owes the README. Companion to the T1
> canary (`engine/tests/test_vector_canary.py`) and [ADR-13.2](../office-hours/09-decisions.md#adr-13).

## What the T1 canary proved, and what it did not

The day-one canary (2026-07-17) proved the storage bet: a `VECTOR(512)` column and a C-SPANN
`VECTOR INDEX` can be created on our tier, K-NN via `<->` returns exact expected ordering on
normalized vectors, and `EXPLAIN` shows the query plan using the index.

It proved that for an **unfiltered** K-NN — `ORDER BY embedding <-> $1 LIMIT k` over the whole
table. That is not the query the product runs.

## What the product actually runs

Every read in this engine is user-scoped, and recall additionally excludes superseded rows and
rows still awaiting backfill:

```sql
SELECT id, type, event_time, confidence, provenance, summary,
       embedding <-> %(qvec)s::VECTOR(512) AS distance
FROM memories
WHERE user_id = %(user_id)s
  AND status = 'active'
  AND embedding IS NOT NULL
ORDER BY embedding <-> %(qvec)s::VECTOR(512)
LIMIT %(top_k)s
```

Measured with `EXPLAIN` against the real CockroachDB Cloud cluster (2026-07-23):

| Query shape | Plan |
|---|---|
| Unfiltered (canary shape) | ✅ `vector search` on `memories@memories_embedding_idx` |
| **Product shape** (`user_id` + `status` + `IS NOT NULL`) | ❌ scan of `memories@memories_user_type_time_idx` + top-k sort — **no vector search** |

A follow-up probe on a scratch table tested whether a *prefixed* vector index
(`VECTOR INDEX (user_id, embedding)`) recovers it:

| Filter | Plan |
|---|---|
| Prefix only (`user_id = …`) | ✅ vector search |
| Prefix + `status = 'active'` | ❌ falls back to a scan |
| Prefix + `embedding IS NOT NULL` | ❌ falls back to a scan |
| Prefix + both | ❌ falls back to a scan |

So on this version, **any residual filter beyond the index prefix makes the planner abandon
the vector index.**

## The decision: keep the filters, accept the scan

The filters are not optional:

- `user_id` is the scoping boundary (ADR-13.4). Non-negotiable.
- `status = 'active'` prevents a superseded note from resurfacing alongside the typed events
  that replaced it — dropping it would reintroduce the double-count the reprocess path exists
  to prevent (ADR-13.5).
- `embedding IS NOT NULL` skips rows awaiting backfill (T15); without it the comparison is
  undefined for those rows.

This is a **version limitation, not a missing index**: no available schema change recovers the
index while keeping correct results. Correctness wins, and the cost is bounded — the scan is
per-user, and a demo account's row count (even after Phase 4 replay: low thousands) makes it
a milliseconds-scale operation. T12's latency profile (Phase 5) measures it rather than
assuming.

## The honest scale answer (input for T17)

This is the substance behind the README's "what did you actually do with the tool?" section,
and it should be told straight rather than glossed:

- The distributed vector index is **real, created, and exercised** — the canary is a permanent
  CI test asserting both K-NN ordering and index usage in the plan.
- At the product's current query shape and scale, **the semantic-recall path executes as a
  per-user scan**, because CockroachDB's vector index on this version does not serve K-NN
  under residual filters.
- The index becomes load-bearing exactly when per-user row counts outgrow a scan — the
  lifelong-memory case the product is designed for, and the reason the column, the index, and
  the normalization invariant are in the schema from day one rather than retrofitted.

Claiming the index is doing heavy lifting at demo scale would be false, and a judge who reads
an `EXPLAIN` would find it. Stating the above is both true and a better answer: it shows the
scale boundary was measured, not assumed.

## Maintenance notes

- **Re-measure when CockroachDB's vector indexing improves.** If a future version serves K-NN
  under residual filters (or supports richer prefixed indexes), re-run the two probes above;
  the code needs no change, only the framing does.
- **Related, tracked separately:** when cosine/inner-product distance ships, the unit-vector
  normalization invariant (ADR-13.2) can be revisited — see the TODO in `TODOS.md`.
- **Do not "optimize" by dropping a filter.** Any patch that recovers the index by removing
  `status` or `user_id` from the WHERE clause is a correctness regression, not a speedup.

## Related files

| File | Role |
|---|---|
| `engine/retrieval.py` | `recall_memories` — the filtered K-NN builder |
| `engine/schema.sql` | `VECTOR INDEX memories_embedding_idx`, `memories_user_type_time_idx` |
| `engine/tests/test_vector_canary.py` | T1 canary: index creation, K-NN ordering, plan uses the index |
| `engine/tests/test_retrieval_recall.py` | Recall semantics: ordering, filters, NULL exclusion, isolation |
