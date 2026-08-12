# Implementation Roadmap — Execution Phases

> **Purpose:** day-to-day execution guide. This document groups the engineering backlog
> ([docs/office-hours/11-implementation-tasks.md](office-hours/11-implementation-tasks.md),
> T1–T18) into sequenced development phases. It does **not** replace the backlog (task
> details, sources, and verification steps live there) or the milestone contract
> ([08-roadmap.md](office-hours/08-roadmap.md)); it is the "what do I build this week"
> layer on top of both. Architectural decisions: [09-decisions.md](office-hours/09-decisions.md)
> (ADR-1..13) — settled, don't re-open mid-build.
>
> **Clock:** deadline 2026-08-19. Phase estimates below total ~4.5 weeks of CC-assisted
> work, leaving ~1 week of buffer. Phases 1–4 ≈ Milestone 1 (the submittable spine);
> Phase 5 ≈ Milestone 2; Phase 6 ≈ Milestone 3; Phase 7 ≈ Milestone 4.

---

## Phase 0 — The Assignment & Repo Bootstrap *(pre-code · ~1–2 days, mostly human work)*

- **Objective:** know the demo's true story before building anything, and give the project a
  version-controlled home.
- **Why this phase exists:** OQ5 is the only remaining unknown no tool can answer — if your
  real 6–12 months of records don't contain a demo-worthy causal story, the demo script must
  change *now*, not in week 5. And the docs need history: the repo should be born before code.
- **Related tasks:** none from T1–T18 (this is the un-parallelizable human work the review
  flagged — outside voice #7).
- **Deliverables:**
  - `git init` + first commit containing `CLAUDE.md`, `TODOS.md`, `docs/` (all current docs)
  - MIT or Apache-2.0 `LICENSE` at repo root (Devpost hard gate — visible in About section later)
  - Monorepo skeleton: `engine/ agent/ api/ web/ cli/ evals/` (empty packages, one `pyproject.toml`)
  - **The causal story, written down**: the one dated narrative (e.g. protein ↑ May 12 →
    body fat ↓ Jun 2, lag ≈ 3 wk) with actual dates and numbers from your records, plus a
    named fallback story
  - Raw reconstruction inputs gathered locally (chats, gym logs, reports) — NOT committed
- **Dependencies:** none.
- **Definition of Done:** story doc exists with real dates/numbers and survives the
  sanitization rules (ADR-7 as narrowed by ADR-13.4); repo pushed to GitHub (private is fine
  until submission); `docs/` renders correctly on GitHub.
- **Demo checkpoint:** you can tell the demo story out loud, with dates, in under 60 seconds.
- **Suggested commit milestone:** `chore: repo bootstrap — docs, license, monorepo skeleton`

---

## Phase 1 — Cloud Foundations, Canaries & Deploy-Early *(~2–3 days)*

> **Status 2026-07-19:** T1 ✅ · T2 ✅ (checkpointer fallback landed — see
> [engineering/cockroachdb-postgressaver.md](engineering/cockroachdb-postgressaver.md)) ·
> T10 ✅ **live** ([deploy.md](deploy.md) has the URL; CI→ECR→ECS Express verified
> end-to-end) · T13 ✅ (≈$43–63, inside envelope —
> [budget line-item](office-hours/README.md#budget-line-item-t13-re-derived-2026-07-19)).
> **Sole remaining item: the ccloud recording** → [docs/evidence/](evidence/README.md);
> then the phase is formally complete.

- **Objective:** every external dependency proven or disproven in week one; a live URL exists.
- **Why this phase exists:** the two riskiest bets (CockroachDB vector indexing on your tier,
  PostgresSaver compatibility) are cheap to test and catastrophic to discover late. Deploy
  friction is paid once, now — from here on, every phase improves a *live* app (ADR-11).
- **Related tasks:** **T1** (vector canary), **T2** (PostgresSaver canary), **T10**
  (Dockerfile + CI + ECS Express Mode deploy — originally App Runner, amended
  2026-07-19 per ADR-13.3), **T13** (budget line-item).
- **Deliverables:**
  - CockroachDB Cloud cluster provisioned via **ccloud CLI — screen-recorded** (tool evidence)
  - `VECTOR(512)` canary green against Docker CockroachDB AND once against the real cluster
  - PostgresSaver canary green (or the fallback decision made and recorded in 09-decisions)
  - GitHub Actions CI: lint + pytest with single-node CockroachDB service container
  - Docker image (FastAPI serving a hello page) deployed to **Amazon ECS Express Mode**
    (App Runner closed to new customers — ADR-13.3 amendment) — public URL live
  - Budget line-item written into `docs/office-hours/README.md`
- **Dependencies:** Phase 0 (repo exists).
- **Definition of Done:** CI green on main; both canaries are permanent tests; hitting the
  ECS Express service URL returns the app; ccloud recording saved to evidence folder.
- **Demo checkpoint:** open a public URL on your phone; show CI passing with a real
  CockroachDB in the loop; show the ccloud provisioning recording.
- **Suggested commit milestone:** `feat(infra): cluster, canaries, CI, ECS Express deploy-early`

---

## Phase 2 — Memory Write Path *(~3–4 days)*

> **Status 2026-07-22 (verified against the code):** T3 ✅ · T4 ✅ · T9 ✅ · T15 ✅ — engine
> write path, email+password auth with per-user scoping, and embedding backfill implemented;
> **56 Phase-2 tests green** against real single-node CockroachDB (registry drift canary 6,
> ingestion failure matrix + provenance 15, Bedrock empty-result contract 9, CLI backfill 11,
> auth 6, cross-user scoping 2, reprocess endpoint 7) — **58 collected in total** with the
> two Phase-1 canaries. Full schema (`memories`, `users`, `sessions`, `user_profile`,
> `turns`, `evidence_traces`) is live; `turns`/`evidence_traces` are created but written in
> Phase 6 (T7), photo/S3 vision ingestion is Phase 5 — both intentional scope boundaries.
> Transaction semantics + never-lose-input guarantee documented in
> [engineering/ingestion-transaction-boundaries.md](engineering/ingestion-transaction-boundaries.md).
>
> **Known Phase-2 gaps** (audited 2026-07-21 — see that doc's §13). Fixed 2026-07-21: the
> note fallback no longer hardcodes `provenance='live'`, so replay notes stay `reconstructed`
> when T8 lands (D3); the provider now distinguishes "nothing to log" from "couldn't parse
> this" via a required `no_loggable_content` flag, closing the last silent-input-loss path
> (D1). The CLI backfill entry point gained its own test suite 2026-07-22
> (`cli/tests/test_backfill.py`), and `reprocess_note` is now reachable via
> `POST /api/memories/{id}/reprocess` (D2, 2026-07-22). **No known Phase-2 gaps remain.**

- **Objective:** talking to the app creates trustworthy, typed, never-lost memories.
- **Why this phase exists:** ingestion is half the product (premise 3). Everything downstream
  — retrieval, insights, traces, replay — consumes what this phase writes, so its contracts
  (payload registry, failure policy) must be right first.
- **Related tasks:** **T3** (Pydantic registry), **T4** (16A failure policy), **T9** (auth +
  scoping), **T15** (embedding backfill).
- **Deliverables:**
  - Full schema live: `memories` (+ vector/inverted/secondary indexes), `users`, `turns`,
    `evidence_traces`, `user_profile` ([04-database-design.md](office-hours/04-database-design.md))
  - `engine/types.py` registry — every memory type validated, `extra="allow"`
  - Text ingestion: message → Bedrock extraction → typed events + Titan V2 embeddings
    (normalized, 512-dim); success-direct / note-on-failure / supersede-on-retry
  - Simple email+password auth + sessions; per-user scoping enforced + security test
  - Backfill: opportunistic on next ingest + `cli backfill` command
- **Dependencies:** Phase 1 (DB, CI, deploy pipeline).
- **Definition of Done:** all Phase-2 paths from [12-test-plan.md](office-hours/12-test-plan.md)
  green (routing, failure fixtures, drift canary, scoping test); a curl/API session can sign
  up, log "250g curd, 3 eggs, 200g chicken", and see the typed memory row + receipt payload.
- **Demo checkpoint:** log a meal through the deployed API and show the memory row in
  CockroachDB with nutrition payload, confidence, provenance, and embedding — then kill the
  Bedrock mock/key and show the note-fallback receipt ("saved — parsing incomplete").
- **Suggested commit milestone:** `feat(engine): ingestion write path — typed memories, never-lose-input`

---

## Phase 3 — Memory Read Path & Agent Spine *(~3–4 days)*

> **Status 2026-07-24: COMPLETE** — built in six milestones (M1–M6), all merged to `main`:
> M1 EvidenceTrace contracts + aggregation builders · M2 recall/timeline/lookup builders ·
> M3 context assembly + deterministic ranking · M4 `plan`/`narrate` provider surfaces ·
> M5 typed tool layer + LangGraph spine (+ the checkpoint durability guard) · M6 chat
> endpoint, user-scoped threads, graph lifecycle wiring. **255 tests green** against real
> CockroachDB; version `0.3.0`.
>
> Phase 3 introduced no new T-tasks — it implemented the core design (03/05/06). Fourteen
> decisions were taken *during* implementation and are now recorded as
> [ADR-14](office-hours/09-decisions.md#adr-14): routing-as-tool-selection, the empty-plan
> contract, ingest-before-retrieve ordering, two new builder families, the ranking-recency
> amendment, the two-view evidence split, pure assembly, the citable-surface question that
> **gates T7**, the graph-state durability boundary, engine-injected timezone, strict slots,
> honest per-tool degradation, user-namespaced threads, and the inline-trace API contract.
> Two engineering deep dives were written:
> [graph-state-durability.md](engineering/graph-state-durability.md) and
> [vector-index-and-filtered-knn.md](engineering/vector-index-and-filtered-knn.md).
>
> **Carried into Phase 6:** T7 must resolve the citable-surface contract
> ([ADR-14.8](office-hours/09-decisions.md#adr-14)) before building citation validation.
> **Deferred demo item:** the live-Bedrock demo checkpoint below has not been performed —
> local validation ran against a development provider, so planner/narrator behavior on the
> production model is still unproven.

- **Objective:** the agent answers questions from memory — computed and recalled.
- **Why this phase exists:** this is the eureka thesis made real: "protein in June" is SQL,
  "when did I complain about my knee" is vector search, and the LangGraph agent routes
  between them without ever touching raw SQL.
- **Related tasks:** none new — this phase implements the core design
  ([03](office-hours/03-memory-engine.md), [05](office-hours/05-agent-architecture.md),
  [06](office-hours/06-retrieval-strategy.md)); T-tasks from Phase 2 must be done.
- **Deliverables:**
  - Engine tools: `aggregate_memories`, `recall_memories`, `get_timeline`, `log_memory`
  - LangGraph graph: intent routing (ingest / query / both), PostgresSaver-backed threads,
    Bedrock narration with memory-ID citation markers
  - Bare chat (API or minimal page) answering questions over logged data
- **Dependencies:** Phase 2 (memories exist to read).
- **Definition of Done:** retrieval test block green (aggregations incl. empty-result + tz
  edges, vector top-k with status filter, timeline slices); bare chat correctly answers a
  quantitative and a semantic question about data logged minutes earlier.
- **Demo checkpoint:** log three meals, then ask "how much protein today?" and "when did I
  last eat chicken?" — both answered with citation markers, live on the deployed URL.
- **Suggested commit milestone:** `feat(agent): hybrid retrieval + LangGraph spine + bare chat`

---

## Phase 4 — History Bootstrap (Replay) *(~3–5 days, includes human reconstruction time)*

> **Status 2026-08-02: COMPLETE — all five milestones (M1–M5). OQ5 resolved: GO.** M1 dataset
> contract + converter · M2 idempotent resume ledger · M3 engine support (`ingest_events` + the
> shared persistence tail, `normalize_item`) · M4 orchestration (`cli/replay.py` — resume loop,
> halt threshold + failure artifact, supersession-based corrections, exit codes,
> `--rebuild-ledger`) · **M5 production run**.
>
> **M5 result:** 424 records of the real reconstruction replayed into the live account in 427.8s
> — 0 failed, 0 NULL embeddings, 424 distinct ids, ledger and database in exact agreement,
> **zero extraction calls**. Idempotent rerun: 0 new / 424 skipped in 2.7s. The money question is
> now answerable from the database: Vitamin D **6.20 (2026-03-25) → 38.4 (2026-07-03)** via
> `lookup_events`, with the causal chain reachable by semantic recall.
>
> Decisions promoted to **[ADR-15](office-hours/09-decisions.md#adr-15)**;
> [replay-architecture.md](engineering/replay-architecture.md) remains canonical for mechanism.
> **498 tests green.**

> **Before starting:** [docs/engineering/replay-architecture.md](engineering/replay-architecture.md)
> is the **locked** architecture for this phase — 13 design decisions, risk analysis, the M1–M5
> milestone breakdown, and the testing strategy. Read it before writing any Phase 4 code.
> **Amended 2026-07-30:** structuring the reconstruction moved to dev-time tooling (ADR-10's
> dev-time/runtime split), so **replay makes zero runtime extraction calls** — every record takes
> the direct-ingest path. The extraction cache is removed (re-add trigger recorded in its §8) and
> note-confidence threading left Phase 4 scope.

- **Objective:** your account becomes the mature account — months of real history in the
  production pipeline; the money question becomes answerable.
- **Why this phase exists:** the time-travel demo needs depth. The review originally made replay
  the schedule + cost bottleneck (outside voice #7); the zero-extraction amendment removes that
  bottleneck — the remaining risk is **idempotency**, not cost (duplicate rows would silently
  inflate the aggregates the demo's causal story rests on).
- **Related tasks:** **T8** (replay CLI: converter, idempotent resume, direct-ingest;
  extraction cache removed by the 2026-07-30 amendment).
- **Deliverables:**
  - One-time converter: reconstruction markdown + reviewed composition table → JSONL + manifest
  - Replay CLI feeding those records through the production ingestion pipeline via
    `ingest_events` (confidence-tagged, provenance=`reconstructed`, `expanded_from` on synthetic
    period rows)
  - Idempotent resume ledger + supersession-based correction workflow
  - The full pre-cutover history (~430 records) bootstrapped into your account
  - **OQ5 verified in the database**: the causal story's numbers exist post-ingestion
- **Dependencies:** Phase 2 (ingestion), Phase 0 (reconstruction + composition table),
  **Bedrock access** (embeddings are the one remaining model dependency — ADR-13.2).
- **Definition of Done:** a full run makes **zero extraction calls** (property test); an
  interrupted run resumes without duplicates and a forced double-run produces none; the money
  question's underlying aggregates return the story's real numbers; bare chat answers it with
  dated citations.
- **Demo checkpoint:** ask the deployed bare chat "what changed before my Vitamin D recovered?"
  — dated, real-history answer (event-time framing per ADR-13.10). **This is the submittable
  spine: Milestone 1 complete.**
- **Suggested commit milestone:** `feat(cli): replay — history bootstrapped, money question live`

---

## Phase 5 — Insight Engine *(~5–6 days)*

> **Status 2026-08-06: M0–M5d complete, 718 tests green.** M0 decisions + fixture hygiene
> (`ce4d961`) · M1 insight contracts (`f2d109b`) · EffectScale amendment (`0343958`) · M2
> analytics kernel (`b2d5b25`) · M3 consolidation service + identity (`7385769`) · M4 typed
> retraction (`d45ca8b`) · claim_dates identity fix (`7c49123`) · M5a stage (F₀) hook
> (`489f1cc`) · docs sync (`78f61b1`) · M5b insight family + trace lineage (`43d1f9b`) ·
> M5c `analyze_series` graph dispatch (`8e6f635`) · M5d `cli/consolidate.py` (`2dfb854`) ·
> teardown fix + CockroachDB engineering record (`6d02f15`) · declarative ECS runtime
> configuration (`adb4598`).
>
> **PHASE CLOSED 2026-08-06** — §4 promoted into **ADR-16**. Every insight-engine deliverable
> ships: consolidation at stage (F₀), on-demand `analyze_series`, typed retraction, insight
> retrieval + trace lineage, and the retroactive CLI sweep. Two milestones were resolved by
> decision rather than by build, both deliberately and both recorded:
>
> > ⏸ **M6 (latency profile / T12) is POSTPONED as of 2026-08-06 — owed, not skipped.**
> > It measures the `us-east-1` app → `ap-south-1` CockroachDB hop, so it requires a
> > **verified production deploy**: Secrets Manager configuration confirmed on the *running
> > task*, with real CockroachDB Cloud and Bedrock connectivity. A local benchmark would
> > measure something other than production and would then be cited in an ADR-13.1
> > amendment — worse than having no number. Blockers, deliverables, and the resume point
> > are in [TODOS.md](../TODOS.md) → *M6 — Latency profile (T12)*. ADR-16 therefore carries
> > the consolidation budget number as an **explicitly open field** rather than inheriting a
> > figure nobody measured.
>
> > ✂️ **M7 (photo ingestion) is CUT as of 2026-08-06 — the designated first cut, taken.**
> > It was named first-to-cut when the phase was planned, it is the only Phase 5 deliverable
> > outside the "memory thinks" thesis, it needs S3 + IAM that cannot be verified right now,
> > and it was measured to break conformance tests for all four `ModelProvider`
> > implementations at once — so there is no cheap partial version. Remaining work, in build
> > order, is in [TODOS.md](../TODOS.md) → *M7 — Photo ingestion*. Post-hackathon.
>
> **Consolidation is live in the deployed app as of M5c**: the composition root now builds one
> `ConsolidationService` and shares it between the ingestion tail's stage (F₀) and the graph's
> `analyze_series` node. Before that commit the hook existed but was inert outside tests.
>
> Three approved amendments came out of implementation and are recorded in the architecture
> doc's §11 implementation record: **per-series `EffectScale`** (a single relative effect floor
> refused every clinically meaningful body-composition change), **typed `pre_value`/`post_value`**
> (§4.14's direction-only retraction had no reference to compare against), and **`claim_dates`**
> (fingerprinting the evidence window made an unchanged claim supersede itself once per logged
> day). Measured: consolidation costs **~635 ms/series** cross-region, so ADR-13.1's provisional
> 300 ms completes exactly one series — T12's to re-derive.

> **Before starting:** [docs/engineering/consolidation-architecture.md](engineering/consolidation-architecture.md)
> is the **locked** architecture for this phase (approved 2026-08-03) — 18 decisions, 23
> invariants, risk analysis, the M0–M7 milestone breakdown, and the test strategy. Read it
> before writing any Phase 5 code, exactly as replay-architecture.md gated Phase 4.
>
> **Three things the original plan below did not account for**, all settled by that document:
> **(1)** the detector set changed — measured against the data the Phase 4 replay committed,
> `ruptures` PELT and the 7–35d lag scan have nothing they can honestly run on, and are
> replaced by deterministic `level_shift` + `intervention_outcome` detectors
> ([ADR-13.12 amendment](office-hours/09-decisions.md#adr-13)); `ruptures` is not a dependency.
> **(2) A new deliverable: `cli/consolidate.py`.** Consolidation is event-driven and replay is
> idempotent, so *nothing in the phase as originally written creates insights over the already
> replayed history* — the mature account would ship with zero insights and the money shot's
> "had already flagged it" clause would be false. **(3)** Scope calls: entity canonicalization
> and period-aware aggregation (both handed here by replay-architecture §8) are **deliberately
> deferred** — canonicalization's cheap pre-replay window closed on 2026-08-02 and the
> recommended design does not need it; note-confidence threading rides the photo milestone.
>
> Estimate raised 4–5 → 5–6 days for the added CLI and the M0 fixture work; photo ingestion
> (M7) is the **designated first cut** if the phase overruns.

- **Objective:** the memory thinks: derived insights with lineage, honest scoring, and
  retraction — plus photo logging.
- **Why this phase exists:** two-tier memory (episodic + derived-with-lineage) is the design
  axis no off-the-shelf framework demos; the live "insight appears as you log" beat is the
  demo's proof that consolidation is real (ADR-13.10).
- **Related tasks:** **T5** (typed retraction conditions), **T6** (sync consolidation:
  bucketing, ruptures PELT, bounded lag scan, pattern-strength formula), **T12** (latency
  profile).
- **Deliverables:**
  - Consolidation at stage (F₀) — post-commit, best-effort, budgeted — plus on-demand
    `analyze_series`, graph-dispatched like `log_memory` so the retrieval builder set stays
    read-only
  - Insight rows: hypothesis, `evidence_ids`, `pattern_strength` (published *with* its three
    components), typed `retraction_condition`, honest lifecycle (`retracted`/`superseded`,
    never deleted), one active insight per `(user_id, kind, series_key)`
  - **`cli/consolidate.py`** — one-shot retroactive pass over the replayed history, so the
    mature account actually has insights (truthful `created_at`, event-time framing per
    ADR-13.10)
  - Insight-lookup builder family + insight lineage populated in `EvidenceTrace`
  - Photo ingestion: S3 upload → Bedrock vision → meal events (Milestone 2 item; first to cut)
  - Measured latency profile for ingest/query/both turns (`docs/latency.md`) — measured
    against the **deployed** cross-region path, and ADR-13.1's provisional ~300ms re-derived
    from it
- **Dependencies:** Phase 4 (real series to analyze — insights over synthetic fixtures only
  prove tests, not the product).
- **Definition of Done:** consolidation test block green (changepoint present/absent, budget
  overflow, retraction flip, supersession chain); logging a new body-scan produces or updates
  an insight in the same turn; receipt < 3s perceived or the gap documented with a plan.
- **Demo checkpoint:** log a workout/scan on camera and watch a derived insight appear in the
  same response — `created_at = now`, truthfully.
- **Suggested commit milestone:** `feat(engine): sync consolidation — pattern flags, typed retraction, photo ingestion`

---

## Phase 6 — Evidence Traces & Glass-Box UI *(~5–7 days)* — **ACTIVE**

> **Status 2026-08-12: Today shipped (`8aed61a`) — the first surface added after 07's build
> order.** Not part of the original plan: it comes from the competitive research approved
> 2026-08-12 (P0 #1), which found that all five products studied — MyFitnessPal, Google Health
> (the renamed Fitbit app, since 2026-05-19), WHOOP, Oura and Apple Health — open to a
> Today-style home, while AyuMind opened to a composer. The consequence was concrete rather than
> aesthetic: there was no way to learn where you stood without composing a question, and a judge
> working through a submission in ninety seconds may never compose one.
>
> One new endpoint, `GET /api/today` (`engine/today.py` + `api/routers/today.py`):
> **deterministic and model-free**, composing `fetch_stats`, `get_profile` + `compute_targets`,
> `aggregate_memories` over `protein_g`/`kcal` for today and yesterday, `latest_weight` and
> day-grouped coverage into **one** round trip — six would be the N+1 mistake at page level over
> the `us-east-1` → `ap-south-1` hop this system actually runs on. It returns the
> `RetrievalStep`s it executed, so the screen carries the same "how this was retrieved"
> disclosure a conversational turn does (ADR-12). Spec: [DESIGN.md §6.20](../DESIGN.md); the four
> design decisions, including the rejected streak, are in §16's Decisions Log.
>
> The load-bearing contract is `value: null`, never `0`, for a metric with no logged rows, with an
> explicit `has_data` flag beside it — "you logged nothing" and "you ate 0 g" are different claims
> and only the first is true at 8 AM, and in JS both values are falsy, which is exactly how a
> fabricated zero would reach the most-seen screen in the product.
>
> Two pre-existing defects fixed in the same commit. The larger: `Timeline.tsx` put `sr-only` on
> the `<table>` itself, which cannot shrink a table (tables size to content), so a 114-day account
> carried a clipped 2,784px box that pushed `document.scrollHeight` to three screens of empty
> scroll — invisible while `AppScreen`'s `overflow-hidden` shell was its only consumer, immediate
> on a screen that scrolls. Also three E501s left in `api/routers/profile.py` by the ADR-17 commit.
>
> Verified: 6 new engine tests green against real CockroachDB · 762 Python tests passed · ruff and
> `tsc` clean · **15/15 Playwright green** including both axe assertions (which also closes the
> outstanding full-suite re-run owed since 2026-08-09, see [TODOS.md](../TODOS.md)) · initial
> bundle unchanged at 108.06 KB gzip, Today being 3.65 KB lazy · smoke-tested at 1440×900 and
> 390×844, light and dark, against both the real replayed history and a seeded account. **Next per
> the research: the Review surface (P0 #2) — not started, awaiting approval.**
>
> **Status 2026-08-08: M7 + M8 complete — the 07 build order is fully shipped.** The timeline
> strip renders memory density with changepoint markers, buckets by week and scrolls below 768px
> (a previously-open F-T7 gap, now closed and verified at a 390×844 viewport), and click-to-scrub
> jumps the conversation to the matching turn. Insight lineage cards expand to their pattern
> strength and a rendered retraction sentence — the text list DESIGN.md §13 designated as the
> reasoning-lineage graph's shipped form; the graph itself stays cut. `E`/`T` keyboard shortcuts
> and Esc-blurs-composer landed alongside, plus F-T6's verifiable subset (dvh was already in
> place; added a feature-detected visualViewport listener that re-pins scroll to the last turn
> when the keyboard opens). **772 Python tests green, 15/15 Playwright E2E green** including the
> new mobile-viewport run, zero axe violations, `tsc`/`ruff` clean, bundle unchanged at 106.56 KB
> gzip. **Every item in 07's build order is now shipped or deliberately cut — Phase 6's UI
> surface is functionally complete.** One honest gap against this phase's own Definition of
> Done: it names **4** required Playwright E2E paths (signup→log→receipt→pane; money
> question→chips→trace; slow-Bedrock UX; cross-user denial) and only the first two exist as
> dedicated specs. The properties are not unverified — cross-user denial (I-28) is asserted
> per-route in `api/tests/test_glassbox.py`, and the slow-turn staged-progress line (M6) is
> exercised indirectly by every E2E turn, which all take several real seconds — but neither has
> a Playwright spec written for that scenario specifically. Left for Phase 7 rather than built
> now, to keep this session's scope at M6–M8. What remains beyond that is hardening: the F-T6
> residual (a literal `position: fixed` visual-viewport composer, deferred pending real-device
> access), the M6 latency profile (Phase 5, postponed pending AWS), and Phase 7's
> evals/evidence/submission work.
>
> **Status 2026-08-08: M6 complete — the engine pane narrates itself live.** `POST
> /api/chat/stream` streams real per-stage progress (`retrieving`, `assembling context`,
> `generating`, …) as the LangGraph turn actually runs; the frontend falls back to the
> already-tested plain endpoint automatically whenever the stream fails to establish or complete,
> so the unproven-ALB risk DESIGN.md §11 flagged is handled at runtime rather than by a guess made
> now. Verified against the real dev stack (raw SSE curl + **14/14 Playwright E2E green**, zero
> fallbacks observed in the access log); the actual deployed ALB hop remains unverified pending
> the AWS access that is still blocked.
>
> **Status 2026-08-08: M5 complete — the glass box is interactive.** Citation chips resolve to
> hydrated database rows, clicking one highlights and scrolls to its evidence row, the executed
> SQL is shown with its bound parameters, and history stays inspectable via per-turn trace
> fetching. Mobile gets the evidence drawer on the same gesture. **14/14 Playwright E2E green**
> (Definition-of-Done paths 1 and 2) with axe assertions.
>
> **Status 2026-08-08: M4 complete — the SPA is live end to end.** Landing, auth, app shell,
> guided first turn, engine pane and the design-system primitives all ship, verified with **8/8
> Playwright E2E** (real API + real CockroachDB, axe assertion included), zero WCAG 2.2 AA
> violations on all four routes, and a 106 KB gzip initial bundle. **M5 (chat + chips + receipts)
> is next.**
>
> **Status 2026-08-07: frontend foundation (F0–F5) complete in `fa2dcd5`.**
> Status table, verification evidence, and carried-forward risks:
> **[DESIGN.md §0](../DESIGN.md#0-frontend-foundation-status)**. The design system is
> locked in **[DESIGN.md](../DESIGN.md)** (approved as the M4–M8 visual contract) with the
> engineering contract in
> **[engineering/frontend-guidelines.md](engineering/frontend-guidelines.md)**. `web/` is
> scaffolded on the approved stack — Vite 8 · React 19.2 · Tailwind v4 · Base UI 1.6 · Motion 13 ·
> TanStack Query 5 · Zod 4 — with tokens live, the SPA served from the API container
> ([api/spa.py](../api/spa.py), 12 tests), a Node 24 Docker stage, and a CI frontend lane.
> A `/plan-design-review` pass took the plan 7/10 → 9/10 and added six decisions: the guided first
> turn (§9.1), auth screens (§6.17), 401 draft preservation (§6.11.1), a 72ch conversation cap,
> the `uncited` citation line, and mobile keyboard + timeline-rail behavior.
>
> **The review's sharpest finding:** ADR-13.4 gives every judge an empty account with no seed data,
> so *signup → first message → first receipt* **is** the live product experience being scored, and
> it was the least-specified surface in the plan. §9.1 now specifies it.
>
> **Status 2026-08-06: M0–M3 complete — the glass box's whole backend.** M0 design lock
> (`5aef6f5`) · M1 trace persistence at stage (G) + `citable_ids` (T7a, `32e2e1b`) ·
> M2 mechanical citation validation (T7b, `f652732`) · M3 glass-box read API + batch
> hydration (T16). Every artifact Phase 5 produces is now persisted, validated, and
> fetchable; what remains in this phase is rendering it. Approved scope for this pass is **M0–M3 only** — trace
> persistence, citation validation, and the read API. The frontend (M4–M8) waits on a separate
> design-system, component-library, and frontend-engineering-rules effort that will govern
> every React component.
>
> **M1 resolved ADR-14.8**, which three documents carried as an open item (03 §6, 05 answer
> contract, 09 ADR-14.8): the persisted trace now carries its own citable set, so a validator
> has one source and a valid citation of *aggregated* data can no longer be flagged invalid.
> M1 also closed a test-cleanup gap it had itself opened — stage (G) writes `turns` and
> `evidence_traces` on every graph-driven turn, and neither table was in the purge list.
>
> **Before starting:** [docs/engineering/glass-box-architecture.md](engineering/glass-box-architecture.md)
> is the **locked** architecture for this phase — it resolves Q1 (insight lineage is rendered,
> never cited) and ADR-14.8 (the trace carries its own citable set), and it **amends ADR-13.14
> and ingestion-transaction-boundaries.md §12**: turn and trace persist at a new stage **(G)**
> after narration, not inside the memories' transaction. That amendment is not a convenience —
> the trace does not exist when the ingestion transaction commits, and honouring the original
> wording would hold the never-lose-input transaction open across an LLM call.

- **Objective:** the full wireframe-v3 experience — conversation-first, memory transparently
  visible, every claim clickable to proof.
- **Why this phase exists:** the glass box is what makes the Memory Engine *scoreable*:
  judges see evidence rows and executed queries, not claims. ADR-12 (deterministic traces)
  is what the UI renders.
- **Related tasks:** **T7** (trace persistence + citation validation), **T11** (empty
  states), **T16** (batch-fetch + stats caching).
- **Deliverables:**
  - EvidenceTrace emitted by every assembly, persisted with the turn (one transaction),
    fetched by the UI via app API; citation validation with the honestly-scoped guarantee
    (ADR-13.13)
  - Vite+React SPA per the [07 build order](office-hours/07-glass-box-ui.md): chat + chips →
    evidence rows → receipts → **empty states** → live pane (SSE) → query display → timeline
    → lineage graph (first-to-cut)
  - Batch evidence fetches; cached timeline/stats queries
- **Dependencies:** Phase 5 (insights to display); trace/API contract from T7 gates the UI
  components (Lane E).
- **Definition of Done:** the 4 Playwright E2E paths green (signup→log→receipt→pane;
  money question→chips→trace; slow-Bedrock UX; cross-user denial); a brand-new empty account
  looks inviting, not broken; trace property test holds (no context without trace).
- **Demo checkpoint:** the full money-shot walkthrough on the deployed URL: ask → cited
  answer → click chip → evidence rows with provenance/confidence → "how this was retrieved"
  showing the actual SQL + vector queries.
- **Suggested commit milestone:** `feat(web): glass-box UI driven by deterministic evidence traces`

---

## Phase 7 — Hardening, Evals & Submission *(~4–5 days, finish ≥3 days before Aug 19)*

- **Objective:** the judged package: quality-proven, evidenced, documented, submitted.
- **Why this phase exists:** submissions fail on checklists, not code. Tool evidence,
  license visibility, video, and honest write-ups are scored surfaces.
- **Related tasks:** **T14** (live-model eval lanes), **T17** (honest vector-index framing in
  README), **T18** (demo script per 13A).
- **Deliverables:**
  - Extraction + citation evals green against the live model (pre-demo checklist)
  - Observability + failure-mode story documented (production-readiness criterion)
  - README: setup/run, architecture diagram, **tools write-up** (vector indexing runtime,
    MCP dev-time sessions with logs, ccloud recording) — including the honest scale answer
  - Demo script rewritten (event-time framing; live insight beat); <3-min first-person video
    recorded, uploaded public
  - Repo public with license visible; Devpost submission complete
- **Dependencies:** Phase 6 (the product being filmed).
- **Definition of Done:** every Devpost hard gate checked; evals green within 48h of
  submission; a stranger can clone the repo and run it from README alone.
- **Demo checkpoint:** the video itself — capture → receipt → money question → glass box →
  live insight.
- **Suggested commit milestone:** `chore: submission package — evals, evidence, video, Devpost`

---

## Task → phase index

| Task | Phase | | Task | Phase |
|---|---|---|---|---|
| T1 vector canary | 1 | | T10 Docker/CI/ECS Express | 1 |
| T2 PostgresSaver canary | 1 | | T11 empty states | 6 |
| T3 payload registry | 2 | | T12 latency profile | 5 |
| T4 ingestion failure policy | 2 | | T13 budget line-item | 1 |
| T5 retraction conditions | 5 | | T14 live eval lane | 7 |
| T6 sync consolidation | 5 | | T15 embedding backfill | 2 |
| T7 traces + validation | 6 | | T16 batch-fetch/caching | 6 |
| T8 replay CLI | 4 | | T17 honest index framing | 7 |
| T9 auth + scoping | 2 | | T18 demo script | 7 |

**Working rhythm:** one phase = one PR-sized arc ending at its commit milestone; run the
phase's test block before moving on; if a phase overruns, cut from the top of the [07 build
order](office-hours/07-glass-box-ui.md) / defer P3 tasks — never from canaries, tests, or the
never-lose-input policy. After Phase 4 you always have a submittable product; protect that
property.
