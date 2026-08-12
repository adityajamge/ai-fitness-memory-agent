# TODOS

## ~~Full Playwright suite pending re-run — Timeline month-tick removal (2026-08-09)~~ CLOSED 2026-08-12

- **Resolved.** The full suite was re-run during the Today milestone (`8aed61a`), which touched
  `Timeline.tsx` again and so needed the same coverage: **15/15 green** — `first-run.spec.ts` 9/9
  and `glass-box.spec.ts` 6/6, including both axe assertions and the 390×844 mobile-timeline test.
  Run against a local `uvicorn` on the test cluster via `E2E_BASE_URL`. The month-tick removal and
  the `sr-only` table fix are both covered by that run.

## Narration arithmetic is unchecked (2026-08-12) — the glass box's weakest seam

- **What:** asked "how much protein did I average this week?" on a seeded account, the model
  answered **"You averaged approximately 5.7g of protein this week"** while correctly citing eight
  days of 28–62 g in the same sentence. Every chip resolved, the evidence pane was right, the
  citation report passed. The *prose* was wrong by an order of magnitude.
- **Why it matters more than a missing feature:** this is the one failure mode that damages the
  product's actual thesis. A stateless chatbot being wrong is expected; a system that renders the
  rows *and* a contradictory number teaches the reader not to trust the rows either. It is also
  exactly the discrepancy Oura's own help page concedes about Advisor ("discrepancies may appear
  between the data in Oura Advisor and what's displayed in other sections") — the thing AyuMind's
  citation validator was built to make impossible, and does not yet cover.
- **Root cause:** `engine/citations.py` validates that every marker *resolves to a citable ID*
  (ADR-13.13's honestly-scoped `valid`). It does not, and by design cannot, check that a number in
  the prose matches the aggregate the engine computed. `AggregateResult` already holds the true
  value; nothing compares the two.
- **Smallest honest fix:** the aggregate's own value is deterministic and already in the trace, so
  the narrator does not need to compute it — pass it in the assembled context as a pre-formatted
  figure and instruct narration to quote rather than derive. A validator that regex-scans for
  numbers and diffs them against `AggregateResult` is the heavier alternative and probably the
  wrong shape (it would flag legitimate rounding and prose like "about half").
- **Not in scope for Today.** Filed from the Today smoke test; belongs to the agent/narration lane.

## Today: two deliberate deferrals (2026-08-12)

- **No trend chart yet.** `§6.15`'s series line still does not exist, so Today shows values without
  a curve. Research P1; the Review surface is where it earns its place, and building it for Today
  alone would be a component with one caller.
- **`GET /api/today` has no API-level test.** The engine composer has six
  (`engine/tests/test_today.py`); the router itself is transport-only and untested, unlike its
  siblings in `api/tests/test_glassbox.py`. Worth ~20 lines when the deploy work quiets down —
  specifically an I-28 scoping assertion, since every other read route has one by policy.

## Target-adherence insights (deferred by ADR-17, 2026-08-11) — not built

- **What's deferred:** a derived insight kind for "you hit your protein target N% of days since
  the target changed" — a claim with pattern strength and evidence, the same tier as
  `level_shift`/`intervention_outcome`, not just today's raw numbers next to a target.
- **What already exists to build it on:** `profile_change` gives the target's active-on-date
  history to join against, so a detector can compare each day's logged total to the target that
  was actually in force that day rather than today's. Today (`/app/today`) reads the *current*
  target only — it is composition, not a new detector, and does not touch this.
- **Where it belongs:** Nutrition/Insights product-surface work, per ADR-17's own "Open" section
  — deliberately left for the research pass that follows the profile/onboarding + Today
  foundation, not this milestone.

## M6 — Latency profile (T12): POSTPONED 2026-08-06, not skipped

- **Status:** intentionally deferred, still **owed**. Phase 5 is not complete without it, and
  it is a scored deliverable (T12, `docs/latency.md`). This is a scheduling decision, not a
  cut. The item to cut, if one must be cut, is M7 — see `consolidation-architecture.md` §7.
- **Why deferred:** the whole value of T12 is the **cross-region hop** — the app runs on ECS
  in `us-east-1`, the CockroachDB Cloud cluster is in `ap-south-1`. A local measurement would
  not measure the thing the number exists to describe, so producing one now would be worse
  than producing none: it would put a wrong figure into an ADR amendment.
- **Blocked on (all three, in order):**
  1. The AWS-side prerequisites in [docs/deploy.md](docs/deploy.md) → *One-time AWS setup for
     the above*: two Secrets Manager secrets, `secretsmanager:GetSecretValue` on the execution
     role, a task role with `bedrock:InvokeModel`, `iam:PassRole` on it for `ci-deploy`, and
     three GitHub repo variables. Not done — the local AWS session was expired (`aws login`).
  2. A completed deploy from `main` carrying commit `adb4598` (declarative config) or later.
  3. Verification that the **running task** actually has the config, plus a real signup +
     ingest proving CockroachDB Cloud and Bedrock connectivity. A green `/healthz` proves
     nothing here — `api/main.py`'s lifespan tolerates an unreachable database by design.
- **What M6 must produce when resumed:**
  - `docs/latency.md` — ingest turn, query turn, and both, measured against the deployed URL.
  - A confirm-or-amend verdict on **ADR-13.1's 300 ms** consolidation budget. It is already
    known to be wrong in the right direction: §11.3 of `consolidation-architecture.md` records
    a **measured ~635 ms per series** app→`ap-south-1`, meaning the 300 ms budget completes
    exactly one series and cleanly defers the rest. The mechanism is correct; the number is
    T12's to re-derive.
  - An answer to open question **Q3** (§10): does the budget number change, and does the
    deferral path need a catch-up trigger?
- **Constraints carried forward:** no infrastructure built solely for completeness;
  instrumentation must not alter the path it measures (`consolidation-architecture.md` §8, M6).
- **Resume from:** this entry + §8 M6 + §11.3's measured table. Nothing was started in code,
  so there is no partial work to reconcile — only the blockers above to clear.

## M7 — Photo ingestion (S3 + Bedrock vision): DEFERRED 2026-08-06, post-hackathon

- **Status:** intentionally deferred, **not abandoned**. This was the designated first-to-cut
  milestone from the day Phase 5 was planned (`consolidation-architecture.md` §7, §8), so
  cutting it is the plan executing as written, not the plan slipping.
- **Why cut, specifically:**
  1. **It is the only Phase 5 deliverable that is not the "memory thinks" thesis.** Insights,
     lineage, retraction, and the glass-box evidence they feed are what judges score. Photo
     logging is a nice input channel with zero consolidation value.
  2. **AWS blockers that gate verification, not just work:** an S3 bucket, a task-role policy,
     and a presigned-upload path. The local AWS session is expired, so M7 would land in the
     same code-complete-but-unverified state M6 is postponed *for*.
  3. **Measured blast radius (2026-08-06):** `ModelProvider` is `@runtime_checkable`, so
     adding `extract_from_image` immediately breaks the conformance tests for **four**
     implementations — `BedrockProvider`, `ClaudeAPIProvider`, `CompositeProvider`,
     `FakeModelProvider`. Verified by making the change and running the suite:
     `test_provider_contract`, `test_provider_selection`, and `test_claude_api_provider` all
     failed on `isinstance(..., ModelProvider)`. The change was reverted; the tree is clean.
     **There is no contracts-only first lane** — M7's first commit is necessarily the protocol
     plus all four providers.
- **What remains, in build order:**
  1. `engine/model.py`: `VisionError` + `extract_from_image(s3_key, *, now, tz, caption)` on
     the Protocol, with the same three-outcome contract as `extract_events` (typed events /
     affirmed-empty / raise). Draft wording is in the reverted probe — see §4.17 for the rule
     it must encode: a provider that cannot tell "no loggable content" from "I failed to
     parse this" **must raise**.
  2. All four provider implementations above, or the conformance tests stay red.
  3. An S3 seam (upload before extraction — the bytes are durable *first*, so a vision failure
     loses nothing) plus bucket + IAM task-role policy.
  4. `engine/ingestion.py`: the photo branch. `NotePayload.text` stays **required**; a failed
     vision turn writes the caption when present, otherwise the honest literal
     `"[photo, not parsed]"`, with `photo_s3_key` as an extra payload key.
  5. `_NOTE_CONFIDENCE` parameterised (default 1.0 for live chat) so a photo fallback can
     carry a lower value — this is what closes the note-confidence TODO below.
  6. `api/routers/ingest.py` photo route; `Dockerfile` if the S3 client needs anything.
  7. Tests per §8 M7: vision success → typed meal with `photo_s3_key`; vision failure → note
     with the honest literal + the key; S3 failure → turn persists with a partial-save
     message; the three-outcome contract holds for the vision surface.
- **Invariant it owes:** **I-23** — a photo turn persists something for every failure outcome.
  Already written into §5's invariant table; it is unimplemented, not withdrawn.
- **Docs owed on resume:** `ingestion-transaction-boundaries.md` (§4 photo branch, §9 matrix),
  and close the note-confidence entry below.
- **Do not:** make `NotePayload.text` optional to accommodate a textless photo note. That
  weakens never-lose-input for the *text* path it was written for, and §4.17 rejected it
  explicitly. The literal marker is the decided answer.

## Production abuse & spend controls (deferred 2026-07-12, /plan-eng-review D14)

- **What:** Layered abuse/spend protection for the public app: per-account daily model-call
  budget, global daily spend cap that flips the app to read-only with an honest banner, per-IP
  signup throttle, optional email verification.
- **Why:** Open signup + per-message Bedrock cost = unbounded spend under abuse. Deferred
  deliberately (builder decision): the current focus is the Memory Engine, the hackathon, and
  the portfolio; simple email+password auth only. See ADR-13.15 in
  `docs/office-hours/09-decisions.md`.
- **Pros:** Budget becomes mathematically bounded; read-only degradation is itself a
  production-readiness story.
- **Cons:** ~half a day of work (CC: ~4-5h) touching auth middleware, a usage-counter table,
  and config; adds operational knobs to maintain.
- **Context:** The reviewed design (2026-07-12) was: usage counters keyed by user_id/day in
  CockroachDB, checked in the model-interface wrapper (single choke point — all Bedrock calls
  already flow through it); global cap read from config; IP throttle at the signup route.
  Stopgap in the meantime: AWS billing alerts.
- **Depends on / blocked by:** Simple auth (Milestone 1) must exist first. Do before any
  post-hackathon public promotion of the URL.

## Note-fallback confidence during replay (`_NOTE_CONFIDENCE = 1.0`)

- **What:** `engine/ingestion.py` writes every note-fallback memory with `confidence = 1.0`,
  justified by the comment *"we're certain the user said it; only the parse is incomplete."*
  That reasoning holds for a live chat turn and **not** for a reconstructed one: when the
  replay CLI (T8) pushes old records through `ingest_text` and extraction fails, we persist a
  note asserting full confidence in text that was itself LLM-reconstructed from memory,
  chat logs, and gym sheets.
- **Why:** confidence is a judged, user-visible honesty signal (04-database-design.md:
  "1.0 for directly observed live data"; reconstructed memories are supposed to be flagged by
  `confidence < 1` **and** `provenance='reconstructed'`). Today the provenance half is correct
  (fixed 2026-07-21, D3) while the confidence half over-claims.
- **Pros:** removes the last place a reconstructed row can look as certain as an observed one;
  makes the glass-box UI's confidence column trustworthy across both provenances.
- **Cons:** ~15 minutes. Needs one product decision — a flat reconstructed-note confidence
  (e.g. 0.6) vs. inheriting the confidence the replay caller already assigned to the batch.
  The latter is better but means threading a value that only T8 will have.
- **Context:** surfaced by the 2026-07-21 Phase-2 audit while fixing D3; deliberately left out
  of that change to keep it scoped to provenance. Not user-visible today because `chat` is the
  only ingestion source in production.
- **Depends on / blocked by:** ~~T8~~ **nothing — unblocked, and no longer a Phase 4 item**
  (updated 2026-07-30). The replay CLI now uses the direct-ingest path exclusively, where a
  validation failure is fatal rather than a note fallback, so **replay can no longer produce a
  reconstructed note** and `_NOTE_CONFIDENCE` is never reached. The issue is real but currently
  unreachable; it resurfaces if Phase 5 photo ingestion — or any future import path — routes
  reconstructed content through `ingest_text`. Rationale:
  [docs/engineering/replay-architecture.md §4.6](engineering/replay-architecture.md).

## Planner tool pairing for ambiguous item follow-ups (surfaced 2026-07-29 manual validation)

- **What:** Improve planner tool-selection guidance so an ambiguously-worded item
  follow-up (e.g. "how many eggs did I have?" after logging "2 boiled eggs") pairs
  `lookup_events` with `recall_memories`, rather than selecting `recall_memories` alone.
- **Why:** `lookup_events`'s own description already recommends issuing both together when
  wording could differ ("grilled chicken" vs. "chicken"), but the planner didn't follow
  that for this phrasing. `lookup_events` needs no query embedding; `recall_memories` does
  — so on a provider that can't embed (the Claude API dev adapter), calling only the latter
  means an answerable question degrades to "nothing logged" when the former would have
  succeeded outright.
- **Pros:** Reduces unnecessary dependence on embeddings for questions the exact-match path
  could already answer; likely a small, scoped prompt-guidance change (`agent/tools.py`
  tool descriptions and/or `PLAN_SYSTEM` in `agent/providers/_prompts.py`).
- **Cons:** Needs live-model validation to confirm the tightened guidance actually changes
  tool selection rather than just reading better; risk of over-pairing (issuing both tools
  on every item question, adding latency/cost) if done too bluntly.
- **Context:** Found during the 2026-07-29 manual validation
  ([12-test-plan.md](docs/office-hours/12-test-plan.md#manual-end-to-end-validation-record--2026-07-29)),
  logging a meal with "2 boiled eggs" and asking "how many eggs did I have for breakfast?"
  immediately after. Not a bug — the degradation path it hit instead behaved correctly
  (honest 200, reported error, no hallucination).
- **Depends on / blocked by:** none; can be picked up any time.

## Clean up the shared CockroachDB Cloud dev cluster (surfaced 2026-07-29 manual validation)

- **What:** Remove accumulated historical test users, threads, and checkpoint rows
  (`users`, `sessions`, `memories`, `checkpoints`, `checkpoint_blobs`, `checkpoint_writes`)
  from the shared CockroachDB Cloud development cluster that don't belong to current,
  active validation/demo data.
- **Why:** The cluster has accumulated ~300+ leftover threads/users across the project's
  history (test runs, manual validation sessions). This caused one flaky, non-reproducing
  failure in a full-suite run — `cli/tests/test_backfill.py::test_main_all_sweeps_users_with_gaps`
  sweeps *every* user in the cluster with a NULL-embedding gap, which took over 2 minutes
  and very likely hit a transient connection hiccup given the volume, rather than a code
  defect (it passed cleanly on an isolated rerun).
- **Pros:** Faster, more stable test runs against the real cluster; removes a source of
  noise when interpreting ad-hoc diagnostic queries during future manual validation (stray
  rows from unrelated historical sessions can otherwise look like current-code anomalies).
- **Cons:** Needs care not to delete anything still wanted for demo/portfolio purposes;
  a one-time manual cleanup, not automatable without deciding a retention policy first.
- **Context:** Found during the 2026-07-29 manual validation
  ([12-test-plan.md](docs/office-hours/12-test-plan.md#manual-end-to-end-validation-record--2026-07-29))
  while investigating a `checkpoint_blobs` channel scan that initially looked like it might
  indicate an M5-1 durability guard violation, before being traced to old data.
- **Partially addressed 2026-07-31 (T8 M2):** the M2 rebuild-perf test seeded 2000
  NULL-embedding rows per run and left them behind; seven runs' worth (14,000 rows) were
  deleted and the test now cleans up in a `finally` block. That cut the NULL-embedding gap
  population `--all` sweeps from ~9,650 rows to ~1,260. The rest of the entry still stands:
  ~550 users with gaps, plus the accumulated threads and checkpoint rows.
- **RESOLVED 2026-08-02 by cluster replacement — but the root cause is NOT fixed.** The old
  cluster reached 8,996 memories across **4,690 users** and began failing full-suite runs
  consistently (~26 attempts, never clean; always `SerializationFailure`/`RETRY_SERIALIZABLE`,
  never an assertion). It was replaced with a fresh cluster; the identical code then passed
  **445/445 on the first run**, which proved the failures tracked cluster state, not code.
  Two findings worth keeping:
  - **`schema_locked` blocks `TRUNCATE`.** CockroachDB v25+ sets `schema_locked = true` on
    tables by default (a changefeed optimization). `TRUNCATE` is a schema change and is
    refused; the psycopg client *hangs and drops the connection* rather than surfacing the
    clean error the web SQL console shows immediately. To clear tables: issue
    `ALTER TABLE <t> SET (schema_locked = false)` **one statement per implicit transaction**
    (batched statements are rejected), truncate, then re-lock. `DELETE` is DML and is not
    blocked, so it is the no-unlock fallback.
  - **The accumulation has a specific cause (see the next entry).** Without fixing it, the new
    cluster drifts back to the same state and M5's `[→PERF]` numbers stop being repeatable.
- **Depends on / blocked by:** none; can be picked up any time.

## ~~DB test fixtures never clean up, so every run permanently grows the cluster~~ — RESOLVED 2026-08-03 (Phase 5 M0)

- **Fixed by** `engine/tests/dbcleanup.py`: a registry of the ids this run mints, plus one
  session-scoped purge in the root `conftest.py`. Registration is automatic at the two choke
  points every test-owned row passes through — the `user_id` fixture and `unique_email()` — so
  no test changed and no new test can forget to opt in.
- **The design decision the entry asked for** was between a per-test teardown, a per-run
  schema, and a `TRUNCATE` reset. All three were rejected: per-test teardown adds a round trip
  to ~500 tests on a suite already taking ~4 minutes (and far worse against a cross-region
  cluster); a per-run schema means DDL per run and a second migration path to keep correct;
  `TRUNCATE` has to fight `schema_locked` (see the entry above) and is a whole-table weapon
  aimed at a per-row problem. The registry deletes **only ids this process minted** — it
  cannot touch a row it did not create, which is a stronger safety property than any sweep,
  and it holds even if `DATABASE_URL` is misconfigured.
- **Verified 2026-08-03:** starting from a cleared test cluster, a full 508-test run leaves
  **`memories` 0, `users` 0, `sessions` 0, `user_profile` 0** — down from +1,455 / +146 / +135
  before the fix. Suite runtime is unchanged (3m48s vs ~4m).
- **Known remainder — LangGraph checkpoint rows: 281 per full run** (70 `checkpoints` + 61
  `checkpoint_blobs` + 150 `checkpoint_writes`, measured 2026-08-03). Agent tests that drive
  the graph directly use raw thread ids (`m5-…`/`guard-…`/`canary-…`) rather than the
  `<user_id>:<thread>` namespacing `api/routers/chat.py` applies, so the prefix purge cannot
  see them. Closing it is the same mechanical change `new_user()` applied to users — register
  the thread id at ~5 call sites in agent test modules — and was deliberately left outside
  M0's scope. Worth doing before the count matters again; at 281/run it is roughly 20 runs to
  the scale that caused the original problem.
- **Escape hatch:** `KEEP_TEST_ROWS=1` leaves everything in place for post-mortem inspection.

<details>
<summary>Original entry (kept as the record of the root cause)</summary>

- **What:** `engine/tests/conftest.py`'s `user_id` fixture mints a fresh UUID per test and
  nothing ever deletes the rows written under it. `memories` has **no foreign key to `users`**,
  so orphan rows are not even cascade-deletable. Every full-suite run therefore adds hundreds
  of permanent rows and hundreds of permanent user IDs to the shared dev cluster.
- **Why:** this is the root cause of the entry above. It is what took the previous cluster to
  4,690 users / 8,996 memories and made `cli/tests/test_backfill.py::test_main_all_sweeps_users_with_gaps`
  — which sweeps *every* user with a NULL-embedding gap — progressively slower and more
  contention-prone until the full suite stopped passing at all. It also makes ad-hoc diagnostic
  queries untrustworthy, since stray rows from old runs look like current-code anomalies.
- **Pros:** makes full-suite runs stable and repeatable indefinitely; keeps M5's performance
  measurements meaningful on re-run; removes a recurring source of false-alarm debugging.
- **Cons:** needs a design decision, not just a patch. Options: a session-scoped cleanup
  fixture deleting rows for UUIDs minted during the run; a dedicated per-run schema/database;
  or a `TRUNCATE`-based reset in `conftest` (which must handle `schema_locked`, above).
  Per-test teardown adds runtime to a suite that already takes ~4 minutes.
- **Context:** Phase 4 made this materially worse — the suite grew from 359 to 445 tests, ~30
  of which write rows — but the gap is pre-existing, dating to the Phase 2 fixtures.
- **Depends on / blocked by:** none. Worth doing **before** the cluster accumulates again;
  it is currently clean (0 rows, fresh cluster, 2026-08-02).

</details>

## ~~CockroachDB `SerializationFailure` flakes~~ — ROOT CAUSE FOUND 2026-08-06: oversized teardown

- **Corrected diagnosis.** This entry first blamed a missing retry path for the 1–2
  non-reproducible `SerializationFailure` failures per full-suite run. That was wrong, or at
  least incomplete. The actual cause was **my own M0 teardown**: the session purge issued a
  single `DELETE FROM memories WHERE user_id = ANY (...)` covering every id the run minted —
  ~200 UUIDs by then — and it was observed running for **1,293 seconds (21½ minutes)** against
  the cross-region cluster before being killed. A write transaction open that long gets its
  timestamp pushed and cannot refresh its read set at COMMIT, which is exactly the
  `RETRY_SERIALIZABLE` the suite kept hitting. Teardown had quietly become the longest, most
  contentious transaction in the run.
- **Why the first diagnosis looked right:** the failures appeared as Phase 5 landed, moved
  between tests, never reproduced in isolation, and always surfaced at COMMIT. Every one of
  those is equally consistent with "more transactions" and with "one enormous transaction", and
  I reached for the first without measuring. The evidence that settled it was `SHOW CLUSTER
  STATEMENTS` showing a single DELETE with a four-figure age.
- **Fixed 2026-08-06:** `engine/tests/dbcleanup.py` deletes in bounded batches
  (`PURGE_BATCH = 50`) rather than one unbounded `ANY(...)`. Each batch is its own implicit
  transaction, so the statement size no longer grows with the suite.
- **Full write-up:** [docs/engineering/cockroachdb-lessons-learned.md](docs/engineering/cockroachdb-lessons-learned.md).

## Retry support for CockroachDB retryable errors (real, and separate from the above)

- **What:** `engine/db.py`'s `Database.transaction()` propagates
  `psycopg.errors.SerializationFailure` (`RETRY_SERIALIZABLE`) straight to the caller.
  CockroachDB classifies it as **retryable** and its documentation states clients must implement
  retry logic. We do not.
- **Still worth doing, even though it was not the root cause above.** A deployed app under
  concurrent load will meet genuine contention that no amount of transaction hygiene removes —
  two users' turns touching the same rows, a long analytical read against live writes. Today
  that surfaces as a failed request rather than a transparent retry.
- **The obvious fix does not work.** "Wrap `transaction()`'s body in a retry loop" is not
  implementable: it is a `@contextmanager`, and while it can catch an exception thrown in at the
  `yield`, it cannot re-execute the caller's `with` block, which lives in a frame it has no
  access to. Retrying a transaction means re-running its statements. Any real fix therefore
  changes the *shape* of the seam:
  - **Option A (smallest):** add `Database.run(work: Callable[[Cursor], T], *, attempts=3)`
    beside `transaction()`, retrying with exponential backoff; migrate hot call sites only.
    Additive; existing callers untouched. Consolidation is the natural first migration — its
    reads are pure and its write is governed by the identity rule (I-12), so a re-run re-derives
    the same claim and writes nothing extra.
  - **Option B:** migrate every call site. Removes the class entirely; touches ~50 sites.
  - **Rejected:** `SAVEPOINT cockroach_restart` — more machinery, and CockroachDB now
    recommends client-side retry instead.
- **Not a correctness risk today:** the failure is loud, not silent — the transaction has
  already rolled back when it surfaces, so nothing is half-written. It costs a failed request,
  not bad data.
- **Depends on / blocked by:** nothing. Best done as its own infrastructure change, ideally
  before Phase 7's production-readiness write-up, since "what happens under contention" is a
  question that section has to answer honestly.

## Drop embedding normalization when CockroachDB ships cosine distance

- **What:** When CockroachDB vector indexes support cosine (or inner-product) distance,
  evaluate removing the unit-normalization requirement on embeddings (ADR-13.2) and update
  the vector canary test accordingly.
- **Why:** Normalization exists solely because C-SPANN is Euclidean-only today (verified
  2026-07-12, https://www.cockroachlabs.com/docs/stable/vector); unit vectors make L2 ≡
  cosine. When cosine ships natively, the workaround is dead weight.
- **Pros:** Removes a non-obvious invariant future contributors could silently break.
- **Cons:** Trivial; re-verifying ranking equivalence takes an hour.
- **Context:** Titan V2 embeddings are normalized at the source (`normalize=true`), so today
  the requirement costs nothing — this TODO is the breadcrumb explaining why it exists and
  when it can die. The canary test asserts K-NN ordering on normalized vectors.
- **Depends on / blocked by:** CockroachDB vector index cosine support reaching the tier we
  run on (roadmap item as of v25.x).

---

# Temporary Architecture Decision Log (post-documentation-freeze)

> **Read this section before starting any milestone.** It is the holding pen for architecture
> decisions **accepted after the office-hours documentation freeze** that are **not yet
> implemented** — decisions that gate upcoming work but have no code to describe yet. Each
> entry carries a **"doc home when implemented"** pointer; once the work lands, migrate the
> entry into its cited ADR/design doc and delete it here.
>
> **Migrated 2026-07-24 (Phase 3 documentation audit):** every entry describing *implemented*
> Phase 3 architecture now lives in [ADR-14](docs/office-hours/09-decisions.md#adr-14) —
> A1/A2/D1/D2 (assembly, builder families, ranking), M4-1/M4-2 (routing as tool selection, the
> empty-plan contract), and M5-1 (the graph-state durability boundary, whose investigation is
> written up in
> [docs/engineering/graph-state-durability.md](docs/engineering/graph-state-durability.md)).
> A3 (the citable-surface contract) is documented as ADR-14.8 and now tracked as a blocking
> decision on **T7** in
> [11-implementation-tasks.md](docs/office-hours/11-implementation-tasks.md).

## Nutrition estimation is LLM-owned and engine-validated (accepted 2026-08-09 — IMPLEMENTED)

- **Status:** ACCEPTED and **implemented** (`engine/nutrition.py`, ingestion stage B½). Listed
  here because it touches a **stated hard constraint** and must be migrated into
  [ADR-13](docs/office-hours/09-decisions.md#adr-13) (or a new ADR-16) rather than living only
  in code comments. **Doc home when migrated:** 09-decisions.md, plus
  [03-memory-engine.md §1](docs/office-hours/03-memory-engine.md) (whose "Bedrock … extracts
  nutrition estimates" line is now wrong — extraction is explicitly forbidden from emitting
  `payload.nutrition`).
- **The bug that forced it:** a meal logged as *"200 gram chicken, 3 rotis, some rice, and
  dal"* persisted with **no `nutrition` key**, so "how much protein yesterday?" hit an
  aggregate filtering `nutrition.protein_g IS NOT NULL`, matched nothing, and answered "nothing
  logged" — while the meal itself recalled perfectly. Root cause: macros were an unenforced
  side effect of the *extraction* prompt, which simultaneously says "NEVER invent facts".
  Verified on the live cluster: `"3 eggs"` produced macros, no macros, and different macros
  (217 vs 225 kcal) across three consecutive turns.
- **What:** two model calls with opposite, coherent instructions. `extract_events` records only
  what the user said (items + quantities, vague amounts preserved in `MealItem.qty_text`);
  a second forced-tool call, `estimate_nutrition`, supplies food knowledge. The engine then
  bounds-checks, sums, and classifies. `payload.nutrition` is **engine-owned**
  (`json_schema_extra={"engine_owned": True}`), so `payload_field_guide()` hides it from the
  extractor — one key, one owner.
- **No food or recipe database, by decision.** Arbitrary dishes ("Chicken Manchurian") resolve
  from model knowledge; the engine asserts only mass balance, the Atwater relation, and a
  protein ceiling — physics, not cuisine. `resolved: false` is a first-class outcome: the food
  is excluded, named in `nutrition.unresolved[]` and in `AggregateResult.excluded_foods`, and
  never assigned an invented number.
- **Three bases stay distinguishable** end to end: `qty_basis` ∈ `stated | ai_estimated`, plus
  exclusion. `confidence_class` (`high|medium|low`) is a pure function of `(qty_basis, kind)`;
  the model's own `model_confidence` rides along for display and **no computation reads it**.
- **The constraint this bends, stated plainly.** The memory layer stays provider-free —
  storage, retrieval, ranking and consolidation still read typed JSONB only, so ADR-1 holds.
  But **stored nutrition values are now provider- and prompt-version-dependent**: replaying the
  same history through a different model yields different numbers. That is why every estimate
  carries `nutrition.method` (`pipeline`, `model_id`, `prompt_version`, `estimated_at`), and
  why the value is **frozen at write time** — aggregation never calls a model, so determinism
  holds everywhere downstream of the write.
- **Rejected:** a curated food table as the source of truth (cannot cover cuisines; becomes a
  maintenance surface), and a recipe/dish database (millions of dishes; dishes decompose).
  Both were designed in full and declined on product grounds — the intelligence belongs to the
  LLM. A tiny canonical table remains *available* for validation only, and v1 ships without one.
- **Open follow-up:** replay currently re-estimates any meal it ingests without reviewed
  macros. It should route through the T8 extraction cache (keyed by content hash) so re-runs
  stay free **and** stable — the same "one estimate per composition" property
  [replay-architecture.md](docs/engineering/replay-architecture.md) already establishes for
  extraction. Not blocking the core flow; `IngestionService(estimate_nutrition=False)` is the
  interim lever.

## Write-side entity canonicalization (accepted 2026-07-23 — before Phase 4 replay)

- **Status:** ACCEPTED architectural decision (not an M3 task). Extends the extraction
  contract; do **not** implement query-time synonym/variant expansion — that was evaluated
  and rejected (see below).
- **What:** During ingestion, the extractor emits a **canonical entity** alongside the
  original logged value on typed items, whenever a canonical form applies. Shape (payload
  hot fields, `extra="allow"` — no migration):
  - Food item: `canonical="chicken"`, `logged="Grilled Chicken"`, `preparation="grilled"`
  - Exercise: `canonical="bench_press"`, `logged="Flat Bench Press"`
  - Supplement/medication: `canonical="vitamin_d"`, `logged="Vitamin D3 60000 IU"`
- **Why:** `lookup_events` uses exact JSONB containment (`@>`) over extracted items. Without
  a canonical name, "when did I last eat chicken?" exact-matches `"Chicken"` but silently
  misses `"Grilled Chicken"` — a *confident* wrong answer, the worst failure class for a
  glass box. Canonical names make the structured path correct by construction; semantic
  recall stays as the fuzzy fallback for whatever canonicalization can't anticipate.
- **Why write-side, not read-side:** normalization runs **once per memory at ingestion**
  instead of on every query forever. It keeps the deterministic engine boundary intact —
  the engine never interprets language (06); canonicalization is the extractor's job (the
  one NL layer already sanctioned on the write path). Query-time variant expansion was
  rejected: a static synonym table rots and can't cover an open, multilingual, personal
  food vocabulary; LLM variant generation is just worse-coverage semantic search with a new
  hallucination surface and run-to-run nondeterminism — and putting either below the
  tool-call boundary would break the "engine never interprets language" invariant.
- **Timing (the reason this is logged now):** the account has no history until Phase 4
  replay (T8). Adopting canonicalization **before** replay canonicalizes all 6–12 months of
  reconstructed history on first ingestion, free. Adopting it after means re-extracting
  history (the T8 extraction cache softens but doesn't eliminate the re-run). This is the
  cheapest window.
- **Scope of change:** extraction prompt + tool schema (`agent/providers/bedrock.py`), one
  or two hot fields per relevant payload type (`engine/types.py`), and `lookup_events` gains
  an optional match on `canonical` (still exact containment, still deterministic). Retrieval
  architecture is otherwise unchanged — this is a data-quality upgrade, not a new path.
- **Independent of this:** add case-insensitive item matching to `lookup_events` regardless
  (mechanical string hygiene, engine-legal, ~zero cost) — a cheap partial mitigation until
  canonicalization lands.
- **Doc home when implemented:** a new ADR in
  [09-decisions.md](docs/office-hours/09-decisions.md) (it changes the extraction contract),
  plus the item-filter paths in [06-retrieval-strategy.md](docs/office-hours/06-retrieval-strategy.md).
- **Depends on / blocked by:** decide and land **with or just before T8** (Phase 4 replay),
  the first pipeline that produces reconstructed memories at scale.
- **Design review:** flagged as a scope gap against the roadmap/backlog (this decision is
  absent from Phase 4's deliverables and T8's estimate) in
  [docs/engineering/replay-architecture.md §5](engineering/replay-architecture.md#5-risk-analysis)
  and tracked as open question 3 in
  [§8](engineering/replay-architecture.md#8-open-questions) — needs an explicit
  in-scope-for-Phase-4 vs. deliberately-deferred call before T8 implementation starts.
