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

- **Objective:** your account becomes the mature account — months of real history in the
  production pipeline; the money question becomes answerable.
- **Why this phase exists:** the time-travel demo needs depth, and the review made replay the
  schedule + cost bottleneck (outside voice #7) — extraction caching is what makes iteration
  affordable while prompts churn.
- **Related tasks:** **T8** (replay CLI: extraction cache, small batches, idempotent resume).
- **Deliverables:**
  - Replay CLI feeding reconstructed events through the production ingestion pipeline
    (LLM-assisted reconstruction, confidence-tagged, provenance=`reconstructed`)
  - First ~3 months bootstrapped into your account; then the full 6–12 months
  - **OQ5 verified in the database**: the causal story's numbers exist post-ingestion
- **Dependencies:** Phase 2 (ingestion), Phase 0 (reconstruction inputs + story).
- **Definition of Done:** second replay run makes zero Bedrock calls (cache proof);
  interrupted run resumes without duplicates; the money question's underlying aggregates
  return the story's real numbers; bare chat answers it with dated citations.
- **Demo checkpoint:** ask the deployed bare chat "what changed before my body fat started
  dropping?" — dated, real-history answer (event-time framing per ADR-13.10). **This is the
  submittable spine: Milestone 1 complete.**
- **Suggested commit milestone:** `feat(cli): replay with extraction cache — history bootstrapped, money question live`

---

## Phase 5 — Insight Engine *(~4–5 days)*

- **Objective:** the memory thinks: derived insights with lineage, honest scoring, and
  retraction — plus photo logging.
- **Why this phase exists:** two-tier memory (episodic + derived-with-lineage) is the design
  axis no off-the-shelf framework demos; the live "insight appears as you log" beat is the
  demo's proof that consolidation is real (ADR-13.10).
- **Related tasks:** **T5** (typed retraction conditions), **T6** (sync consolidation:
  bucketing, ruptures PELT, bounded lag scan, pattern-strength formula), **T12** (latency
  profile).
- **Deliverables:**
  - Consolidation in-request under the ~300ms budget, on-demand `analyze_series`
  - Insight rows: hypothesis, `evidence_ids`, `pattern_strength` (documented formula),
    typed `retraction_condition`, honest lifecycle (`retracted`/`superseded`, never deleted)
  - Photo ingestion: S3 upload → Bedrock vision → meal events (Milestone 2 item)
  - Measured latency profile for ingest/query/both turns (`docs/latency.md`)
- **Dependencies:** Phase 4 (real series to analyze — insights over synthetic fixtures only
  prove tests, not the product).
- **Definition of Done:** consolidation test block green (changepoint present/absent, budget
  overflow, retraction flip, supersession chain); logging a new body-scan produces or updates
  an insight in the same turn; receipt < 3s perceived or the gap documented with a plan.
- **Demo checkpoint:** log a workout/scan on camera and watch a derived insight appear in the
  same response — `created_at = now`, truthfully.
- **Suggested commit milestone:** `feat(engine): sync consolidation — pattern flags, typed retraction, photo ingestion`

---

## Phase 6 — Evidence Traces & Glass-Box UI *(~5–7 days)*

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
