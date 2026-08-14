# 12 — Test Plan (from /plan-eng-review, 2026-07-12)

> Part of the [office-hours canonical docs](README.md). Decisions: tests run against **real
> single-node CockroachDB Docker** locally and in CI (8A / [ADR-13.8](09-decisions.md#adr-13));
> Bedrock is mocked behind the injected model interface everywhere **except** the live-model
> eval lane (9A / ADR-13.9). Frameworks: **pytest** (backend), **Vitest** (web components),
> **Playwright** (E2E). 100% of the paths below ship WITH their feature, not after.

## Coverage map (all paths are planned-GAPs until implemented)

> **Implemented as of 2026-07-24 (Phases 2–3): 255 tests green** against real CockroachDB.
>
> - **Phase 2** — the `engine/ingestion` block except photo/S3 (Phase 5), the whole
>   `engine/types` block, both canaries, signup/scoping user flows, the provider
>   empty-result contract (D1), CLI backfill, the reprocess endpoint (D2).
> - **Phase 3** — the whole `engine/retrieval` block (aggregate incl. empty-result and tz
>   edges, recall incl. status filter and NULL-embedding exclusion, timeline slices, plus
>   `lookup`/`count` for the two families added in [ADR-14.4](09-decisions.md#adr-14)); the
>   `engine/trace` block **except persistence and citation validation** (T7) — the
>   no-context-without-a-trace property holds at the assembly boundary today; the
>   `agent graph` routing block (ingest / query / both / conversational); the provider
>   `plan`/`narrate` contracts; the tool layer's slot validation; and the chat endpoint's
>   transport contract incl. cross-user thread isolation.
> - **New, beyond the original map:** the graph-state durability suite
>   ([ADR-14.9](09-decisions.md#adr-14)) — the checkpointer guard against a real database, an
>   allowlist tripwire, and a pinning test for LangGraph's silent-drop semantics.
> - **Phase 4 (2026-08-02): 445 tests green.** The whole `cli/replay` block below (T8 M1–M4):
>   converter determinism + expansion rules, the resume ledger incl. `rebuild_from_db`, the
>   two new engine entry points (`ingest_events`, `ingest_events_superseding`) and
>   `normalize_item`, and the orchestration loop's resume/halt/correction/exit-code behavior.
>   The load-bearing ones are the **zero-extraction property** and the **forced-double-run
>   duplicate guard**. Detail: [engineering/replay-architecture.md](../engineering/replay-architecture.md) §7.
>
> - **Phase 5 (2026-08-05): 697 tests green.** The whole `engine/consolidation` block below,
>   across M0–M5c: the analytics kernel pinned against the **real** protein series and Vitamin D
>   pair (sanitized fixtures, ADR-7), the insight payload contracts and drift canary, the
>   identity rule, typed retraction, and the stage-(F₀) ingestion hook. The load-bearing ones are
>   **I-12** (recompute over unchanged data writes zero rows, asserted at the committed-row
>   layer), **I-20** (retraction flips `status` and leaves the payload byte-identical), **I-21**
>   (no model, no prose, proven by rewriting a hypothesis to say the opposite and asserting the
>   verdict is unchanged), and **I-8** (no note text reaches a detector). Detail:
>   [engineering/consolidation-architecture.md](../engineering/consolidation-architecture.md) §9, §11.
>   Test hygiene: since Phase 5 M0 a full run leaves **zero** residue for the ids it mints.
>
> **Phase 6 (2026-08-08): 772 tests green, plus 15/15 Playwright E2E.** `engine/trace`'s
> persistence + citation validation block (T7a/T7b), `api/tests/test_glassbox.py`'s read API and
> batch hydration (T16), `api/tests/test_chat_stream.py`'s SSE stage-narration contract (M6), and
> the `pattern_strength`/`retraction` additions to `engine/tests/test_retrieval_insights.py`
> (M8) all landed here. E2E: `web/e2e/first-run.spec.ts` (signup → log → receipt → pane,
> including a dedicated 390×844 mobile-viewport run of the timeline) and
> `web/e2e/glass-box.spec.ts` (money question → chips → trace) are both green with zero axe
> violations. Detail: [engineering/glass-box-architecture.md](../engineering/glass-box-architecture.md),
> [DESIGN.md §0](../../DESIGN.md#0-frontend-foundation-status).
>
> **2026-08-14: Phase 5's M7 (photo ingestion) shipped**, reintroduced from its 2026-08-06 cut —
> as an **ephemeral-storage variant, no S3** (AWS access still unavailable; see
> [engineering/consolidation-architecture.md](../engineering/consolidation-architecture.md) §4.17's
> amendment). New tests: `engine/tests/test_photo_ingestion.py` (5 tests — vision success with
> correct `qty_basis` honesty, vision failure → note fallback, no-food-in-photo noop, validation
> failure → note) and `api/tests/test_chat_photo.py` (6 tests — happy path, auth, unsupported
> content type, malformed image, oversized image, vision-failure-preserves-caption), all green
> against real CockroachDB.
>
> **Still planned:** the latency profile (Phase 5's M6/T12 — postponed, not skipped, see
> `TODOS.md`; measurement tooling `cli/latency_probe.py` now exists and is shaken down locally,
> but the actual production measurement is still blocked on AWS access), **2 of
> the 4** Playwright E2E paths (slow-Bedrock UI state; cross-user denial — the properties
> themselves are tested elsewhere: I-28 cross-user 404s are asserted per-route in
> `api/tests/test_glassbox.py`, and the staged-progress line is exercised indirectly by every
> E2E turn, but neither has a dedicated spec), and both live-model eval lanes (T14 — `evals/`
> holds no golden-set suite yet). All three are Phase 7 scope, not Phase 6 gaps. No dedicated
> Playwright coverage was added for the photo-upload UI either — explicitly out of scope for the
> 2026-08-14 pass per the user's own time-constraint instruction; covered by the backend tests
> above plus manual smoke-testing.
>
> **Naming note:** "M6"/"M7" appear twice in this project with different meanings — Phase 5's
> M6 (latency profile) and M7 (photo ingestion) in `TODOS.md`, versus Phase 6's frontend M6
> (live engine pane) and M7 (timeline strip) in `DESIGN.md` §0 and
> `docs/implementation-roadmap.md`. Both are correct in their own document; check which phase a
> milestone reference is scoped to before assuming which one it means.

```
CODE PATHS                                               USER FLOWS
[+] engine/ingestion                                     [+] Signup → first log
  ├── text → typed events (meal/workout/sleep/            ├── [→E2E] signup → log meal → receipt
  │        scan/weight/supplement/note routing)           │        → memory visible in engine pane
  ├── 16A: extraction fails → note persists,              ├── empty-account states (timeline, stats,
  │        receipt "saved — parsing incomplete"           │        insights, engine pane) — T11
  ├── retry succeeds → typed events supersede note        [+] Mature-account flows (builder data)
  ├── [+] photo → vision extraction (no S3 — in-memory,   ├── [→E2E] money question → cited answer
  │        discarded after the call; 2026-08-14)          │        → chips resolve → trace panel
  ├── vision failure → note (caption or literal),         │
  │        original photo not recoverable (no S3)         │
  ├── embedding fails → NULL embedding row                ├── "protein in June" → aggregate matches
  └── backfill (next-ingest + CLI) re-embeds ✓            │        known account numbers
[+] engine/types (6A registry)                            [+] Interaction edges
  ├── per-type validation accepts extra keys              ├── double-submit same meal (deliberate,
  └── typed hot fields coerce/reject (drift canary)       │        defined behavior)
[+] engine/retrieval                                      ├── [→E2E] slow Bedrock (10s) → UI state
  ├── aggregate: sum/avg, day/week grouping,              ├── session expiry mid-conversation
  │        type+date filters, EMPTY RESULT, tz edges      └── [→E2E] user A cannot read user B's
  ├── recall: vector top-k, status='active' filter,               memories or traces (SECURITY)
  │        NULL-embedding rows excluded
  └── timeline: ordered slice, range edges
[+] engine/consolidation (1A sync + budget, 17A analytics) — M0-M5c done
  ├── real series WITH level shift → insight row, boundary-anchored evidence_ids
  ├── series WITHOUT a qualifying shift → nothing, with a recorded reason (I-22)
  ├── budget exceeded → defers cleanly, ingestion still succeeds
  ├── typed retraction condition met → status='retracted' (never deleted, payload intact)
  ├── supersession chains via superseded_by; retraction leaves it NULL
  ├── pattern-strength formula: documented, deterministic, pinned on REAL fixtures
  ├── PROPERTY: recompute over unchanged data writes ZERO rows (I-12, committed-row layer)
  ├── no prose reaches a detector or an evaluator (I-8 / I-21, structural + behavioural)
  ├── stage (F₀) runs post-commit and never fails a turn (I-14 / I-15)
  ├── insight family returns lineage a snapshot cannot carry; read-only (I-17)
  ├── trace.insights + citable_ids carry insights; their evidence_ids stay out (Q1)
  └── analyze_series is graph-dispatched, writes only via the service, and an insight
           derived this turn is retrievable in the SAME turn (ADR-14.3 for tier 2)
[+] engine/trace (ADR-12)
  ├── PROPERTY: no assembled context without a persisted trace
  ├── trace fields complete (queries, evidence, insights, ranking)
  ├── citation validation: valid / invalid-ID paths (honest scope per ADR-13.13)
  └── trace fetchable by trace_id, user-scoped
[+] agent graph
  ├── routing: ingest / query / both turns
  └── [→EVAL] narrator citation compliance
[+] canaries (permanent)
  ├── VECTOR(512) index + K-NN ordering on normalized vectors (T1)
  └── PostgresSaver on CockroachDB checkpoint round-trip (T2)
[+] cli/replay  (T8 M1–M4 — full block in engineering/replay-architecture.md §7)
  ├── converter (M1): byte-determinism · expansion Rules 1–3 · type mapping ·
  │        the two narrowings · record_id stability · payloads validate
  ├── ledger (M2): drift detection · rebuild_from_db · corrupt-ledger safety ·
  │        record_id guard rejects a non-derivable id
  ├── engine (M3): direct/extract paths yield identical row shape · direct-path
  │        validation failure is FATAL · normalize_item contract + non-goals
  └── main loop (M4): PROPERTY extract_calls == 0 after a full run ·
           idempotent resume after interrupt · forced double-run → NO duplicates ·
           correction reported-then-applied (one txn) · halt at 5 consecutive ·
           failure artifact fields · exit codes 0/1/2/3
[+] LLM extraction: [→EVAL] golden set, tolerance ranges

TOTAL: 33 paths  |  E2E: 4 (Playwright)  |  EVAL: 2 (live-model lane)
```

## Evals (live model — separate lane from mocked CI; manual trigger + pre-demo checklist)

- `evals/extraction.py`: ~30 golden logging messages → tolerance-range assertions on macros,
  meal type, absolute AND relative timestamps ("yesterday", "this morning")
- `evals/citation.py`: ~15 question turns → every factual claim cites a valid trace memory ID

## Failure modes (each new codepath: one realistic production failure)

| Codepath | Failure | Test? | Handled? | User sees |
|---|---|---|---|---|
| Extraction | Bedrock throttles mid-turn | yes | 16A note-fallback | "saved — parsing incomplete" |
| Embedding | Bedrock embed call fails | yes | NULL + backfill | receipt notes pending embedding |
| Photo upload | vision call fails (no S3 in the shipped design — 2026-08-14) | yes | note fallback (caption or literal), same as 16A | "saved — parsing incomplete"; original photo not recoverable |
| Consolidation | scan exceeds budget | yes | defer to on-demand | nothing (by design; insight arrives later) |
| Trace persistence | turn-commit failure | yes | single transaction (13.14) | turn retriable, never half-recorded |
| Citation | model cites bad ID | yes | validation flag | visible flag in UI |
| Scoping | cross-user access attempt | yes (security) | denied at query layer | 404 (indistinguishable from "not found" — existence is not probeable) |
| Replay | interrupt mid-run | yes | idempotent resume | resume command |
| Budget | hostile usage exhausts Bedrock spend | no | none (deferred, ADR-13.15) | model-call errors → note-fallback (non-silent) |

**Critical gaps (no test AND no handling AND silent): 0.** The budget row is a deliberate,
documented acceptance (TODOS.md), and its failure mode is non-silent thanks to 16A.

## Manual end-to-end validation record — 2026-07-29

A full manual pass through the running app (fresh user, fresh thread), complementing the
automated suite above with a real client (PowerShell), a real CockroachDB Cloud cluster, and
the real Claude API dev provider (`claude-haiku-4-5-20251001`) rather than mocks — the point
being to catch what mocked tests structurally cannot: real model output shapes, real
provider errors, real connection behavior over a long session. All 10 steps passed by the
end of the session; three defects were found and fixed along the way, each with a regression
test; one additional line of investigation (M5-1 durability) turned up nothing but is
recorded because the false alarm is itself worth not re-investigating next time.

### Step-by-step walkthrough

| # | Step | What was exercised & the evidence that confirmed it | Result |
|---|---|---|---|
| 1 | Startup | Startup log showed `Application startup complete` with **no** `schema setup skipped` or `agent graph unavailable` warnings — proof the schema apply, checkpointer `.setup()`, and `build_graph()` all succeeded against the real cluster. | ✅ Pass |
| 2 | Health / landing / wiring | `GET /healthz` → `{status: ok}`; landing page content matched `Phase 3`; an **unauthenticated** `POST /api/chat` returned **401**, not 503 — the specific signal that distinguishes "not logged in" from "graph never built." | ✅ Pass |
| 3 | Signup / auth | Signup returned `user_id` + `email`; the `session` cookie came back `HttpOnly=True`; `SELECT ... FROM users`/`sessions` showed the new row with a live (unexpired) session. | ✅ Pass |
| 4 | Conversational turn | `Chat "hey there"` produced a natural greeting (not a "no logged data" refusal); `citations`/`receipts`/`errors` all empty; `trace` was present (not null) with an honestly empty `retrieval_steps` — validates the empty-plan contract (M4-2) and trace-by-construction (ADR-12) in one turn. | ✅ Pass (after BUG-3 fix, see below) |
| 5 | Memory logging + embedding degradation | First attempt: `Chat "I had 250g curd, 3 eggs and 200g grilled chicken for lunch today"` fell back to a `note` (`parse_status: "incomplete"`) — this was **BUG-3**. After the fix and a server restart, the identical call produced a typed `meal` row with `items[].qty_g` as real floats (`250.0`, `200.0`), `nutrition.protein_g: 68.0`, and the expected `WARNING engine.ingestion: embedding failed; ... backfill pending` (correct degradation for a provider with no embeddings endpoint). | ✅ Pass (after fix) |
| 6 | Retrieval — 3 families | **Aggregate:** "how much protein did I eat today?" → `68g`, cited, `retrieval_steps[0]` = `{family: aggregate, row_count: 1}`, SQL fully parameterized (`%(user_id)s`, `%(path)s`, `%(start)s`, `%(end)s` — no literals spliced in). **Lookup/count:** "how many workouts have I logged this month?" → honest `0`, no fabricated count. **Timeline:** "what did I do today?" → correctly surfaced and cited the just-logged meal. | ✅ Pass |
| 7 | Citations resolve + fidelity | `GET /api/memories/{cited-id}` → **200** with `type/confidence/summary` populated. Independently, raw SQL `SUM(payload->'nutrition'->>'protein_g')` over today's meals returned **68.0** — an exact match to the number the narrator put in prose, proving the citation isn't just present but numerically honest. | ✅ Pass |
| 8 | Thread continuation + M5-1 durability | Follow-up `Chat "and how many eggs was that?"` came back with zero retrieval and a generic "nothing logged" answer — root-caused as the **summary-generation gap** (below), not a planner failure. Separately, an unscoped `SELECT DISTINCT channel FROM checkpoint_blobs` turned up channel names (`trace`, `in_dict`, `in_list`) that looked like a possible M5-1 violation; investigated and cleared — see the dedicated note below. After the summary fix, a newly logged meal's `summary` came back as `"Breakfast: 2 boiled eggs, 30g peanut butter"` (concrete quantities present, where the pre-fix wording would have been vague). | ✅ Pass (after fix + investigation) |
| 9 | Thread isolation (security) | Second user "bob" presenting Alice's `thread_id` got a genuinely fresh, empty thread — zero awareness of Alice's meals. `GET` on Alice's memory id as Bob → **404** (indistinguishable from "doesn't exist," per the scoping posture). A scoped `checkpoints` query showed two rows sharing the same client-supplied thread suffix but different user-UUID prefixes — the concrete DB-level proof that namespacing actually separates the two threads. The *first* attempt at this step crashed with a raw 500 — this was **BUG-4**, root-caused and fixed mid-step, then the step was re-run clean. | ✅ Pass (after fix) |
| 10 | Error handling | (a) Unauthenticated → **401**. (b) Empty message → **422**. (c) 129-char `thread_id` → **422**. (d) A semantic-only question ("when did I complain about my knee?") on a provider with no embeddings → `errors` correctly named `recall_memories` + "cannot embed"; answer stayed honest, no hallucination. (f) Garbled input ("ate abt haf a plate of teh usual") — Haiku actually parsed it into a typed `meal` rather than falling back to a note, an even stronger pass than the minimum bar (typed **or** honest note, never silently dropped). (e) A deliberately invalid `ANTHROPIC_API_KEY` (set as a shell-only env var, `.env` never touched) produced a real Anthropic `401` → mapped to **502** with the expected `chat turn failed for user ...` warning; key restored afterward and confirmed via a clean restart. | ✅ Pass |

### Defects found, root cause, and fix

**1. Extraction schema gave no type hint for numeric fields**

- *Discovered:* Step 5, first attempt. Server log: `engine.ingestion: validation failed for user ...: 2 validation errors for MealPayload — items.0.qty Input should be a valid number, unable to parse string as a number [input_value='250g'] ... items.2.qty [input_value='200g']; note fallback`.
- *Root cause:* `payload_field_guide()` (`agent/providers/_prompts.py`) listed field *names* only (`qty_g`, `qty`, ...), never their type. Nothing told the model these must be bare numbers, so it wrote `"250g"`/`"200g"` as strings and Pydantic validation rejected the whole event.
- *Reasoning:* the same guessing-failure shape as an earlier, already-fixed bug where the model invented key names (`{"food": ...}` instead of `{"name": ...}`) — so the fix extends the *same* generator rather than adding a bespoke rule, since the entire payload registry shares one naming convention (`qty_g`, `distance_km`, `duration_min`, `body_fat_pct`, ... — unit baked into the field name), meaning a single generic fix covers all of them at once rather than just meals.
- *Fix:* `_describe_payload()`/`_scalar_kind()` now annotate scalar fields with a `:number`/`:boolean` kind hint (e.g. `qty_g:number`), and the tool description explains the unit-suffix convention once, generically, with the exact `"200g"` counter-example that broke. `agent/providers/_prompts.py`.
- *Verified:* retest of the identical Chat call → `parse_status: "ok"`, `type: "meal"`, SQL showed `qty_g: 250.0` / `200.0` as real floats. 2 new regression tests in `agent/tests/test_prompts.py`; 126/126 `agent/` tests green at that point.

**2. Evidence summaries carried no quantities**

- *Discovered:* Step 8. The follow-up "and how many eggs was that?" returned `trace.retrieval_steps: []` and a generic "nothing logged" answer, despite the eggs having just been logged in the same thread.
- *Root cause:* traced through `agent/graph.py` (`plan_node`/`narrate_node` only ever see `state["question"]` — the current turn's raw text — never `state["messages"]`; cross-turn continuity is meant to flow through re-retrieval, per [05-agent-architecture.md](05-agent-architecture.md)) and then through all three retrieval query builders in `engine/retrieval.py` (`lookup_events`, `recall_memories`, `get_timeline`): none of them `SELECT payload` — confirmed at the SQL layer, not just in rendering. `render_context()` (`agent/providers/_prompts.py`) only ever prints `memory.summary` for a retrieved memory. So even a perfect retrieval call would have surfaced only the vague, model-written summary text — no item-level quantity was ever fetched from the database in the first place.
- *Reasoning:* the first fix considered was adding `payload` to `EvidenceSnapshot` and the three SQL builders — but `EvidenceSnapshot`'s own docstring (`engine/trace.py`) says it is "**Deliberately payload-free**" ([ADR-12](09-decisions.md#adr-12) — a memory chip resolves through the app API, never a trace copy). That would have silently breached a documented architectural boundary rather than fixed a bug, so the conflict was surfaced explicitly instead of proceeding either way unilaterally. The chosen alternative — improve the model-written `summary` field's guidance — achieves the same outcome without touching the ADR-12 boundary at all, since `summary` already flows unchanged through `EvidenceSnapshot` → `render_context()` → the narrator.
- *Fix:* added a `description` to the `summary` field in `extract_tool_schema()` instructing the model to include concrete item quantities (with a worked example), noting that summary is the *only* text a later question's evidence will ever show. `agent/providers/_prompts.py`.
- *Verified:* a fresh meal ("I had 2 boiled eggs and 30g peanut butter for breakfast") produced `summary: "Breakfast: 2 boiled eggs, 30g peanut butter"` — quantities now present where the pre-fix wording ("Lunch with curd, eggs, and grilled chicken") had none. 1 new regression test; 127/127 `agent/` tests green. Note: the *immediate* follow-up question still didn't resolve on retest, but for the separate, already-documented reason that `recall_memories` can't run on this dev provider (no embeddings) and the planner didn't pair it with the embedding-free `lookup_events` — logged as a TODO (below), not a defect.

**3. A dropped DB connection crashed every future chat with a 500**

- *Discovered:* Step 9, first attempt. `Invoke-RestMethod` raised a raw `WebException`; the server log showed a full traceback ending in `psycopg.OperationalError: consuming input failed: server closed the connection unexpectedly`, originating inside `langgraph.checkpoint.postgres.get_tuple`.
- *Root cause:* `api/main.py`'s own comment documents the design: "the checkpointer holds one long-lived connection for the app's lifetime" (contrast with `engine/db.py`, which deliberately opens a fresh connection per unit of work). Roughly a 10-hour idle gap had elapsed between the previous chat call and this one (time spent mid-session on investigation), long enough for CockroachDB Cloud to close that one persistent connection. Nothing in `chat.py` caught `psycopg.OperationalError`, so it propagated all the way to an unhandled 500 — the one failure path in the router that didn't degrade gracefully.
- *Reasoning:* matched the existing "graph unavailable → 503" posture rather than inventing a new status code, and deliberately scoped to catching-and-mapping the error only — reconnection/pooling strategy is a bigger design decision than a validation-session fix, so it was explicitly left out of scope.
- *Fix:* `except psycopg.OperationalError` around `run_turn(...)`, mapped to **503** "chat is temporarily unavailable" — same detail string as the existing absent-graph case. `api/routers/chat.py`.
- *Verified:* `test_database_connection_loss_maps_to_503` (monkeypatches `run_turn` to raise the same exception) — 17/17 `api/tests/test_chat.py` green. Then, more importantly, the *original live incident* was reproduced end-to-end: after the fix and a server restart, Step 9 was re-run from Bob's request onward and completed cleanly, confirming the fix resolved the actual failure, not just a synthetic one.

### Investigation note: M5-1 checkpoint durability (no defect found)

Recorded so a future validation session doesn't have to re-investigate the same false alarm.

During Step 8, `SELECT DISTINCT channel FROM checkpoint_blobs` (unscoped — across the entire
shared cluster) returned `trace`, `in_dict`, `in_list`, and `__start__` in addition to the
M5-1 allowlist (`messages, user_id, question, now, tz, tool_calls, answer, citations`). A
channel literally named `trace` looked like it could mean a banned `EvidenceTrace` object had
been checkpointed — a serious violation, if real.

Investigated rather than assumed:
1. Read `_GuardedSerde._reject` (`agent/checkpointer.py`) — the guard checks by Python
   `isinstance` against the banned *types*, not by channel *name*. A channel merely named
   "trace" isn't automatic proof of a violation.
2. Grepped the current test suite for the literal strings `in_dict`/`in_list`/`guard-canary`
   — zero matches. Nothing in today's code or tests produces those names.
3. Ran the actual M5-1 guard/durability test suite live, against this same database, right
   now: `test_checkpoint_guard.py` + the two real-checkpointer tests in
   `test_graph_routing.py` — **16/16 passed**, including one that runs a rich turn
   (ingest + aggregate + recall) and asserts the persisted channels are a subset of the
   allowlist, with current code, on this cluster.
4. Re-ran the channel query scoped to just this session's own thread — only
   `{__start__, citations, messages, tool_calls}` appeared, a clean subset of the allowlist
   (`__start__` is LangGraph's own internal bookkeeping channel, not one of the banned
   types).

**Conclusion:** the suspicious channels are residue from the shared CockroachDB Cloud
cluster's ~300+ accumulated rows of project history (most likely a pre-M5-1 iteration of the
graph), not a live defect. No code change made; the cluster cleanup is tracked as a TODO
(below) instead, partly so this exact investigation doesn't have to be repeated.

### Findings logged, not changed

Tracked as follow-ups in TODOS.md, not fixed during this session: a planner
tool-selection nuance where an ambiguous item follow-up used only `recall_memories`
(needs embeddings) rather than pairing it with `lookup_events` (doesn't); and the shared
CockroachDB Cloud dev cluster's accumulated historical test data, which caused one flaky,
non-reproducing failure in a full-suite run (`cli/tests/test_backfill.py`) via an unbounded
"sweep every user in the cluster" pattern.

**Suite status at completion:** 127/127 `agent/` tests green; 289/289 full-suite tests green
(one transient connection-related flake on the first full run did not reproduce on an
isolated rerun). All three fixes above shipped with dedicated regression tests.

## Consumed by

`/qa` and `/qa-only` read the sibling artifact at
`~/.gstack/projects/Cockroach-db-hackathon/adity-nogit-eng-review-test-plan-20260712.md`;
this doc is the repo-canonical copy.
