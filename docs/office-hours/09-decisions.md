# 09 — Architectural Decisions, Trade-offs & Rejected Alternatives

> Part of the [office-hours canonical docs](README.md). All decisions below were made
> explicitly by the builder during the 2026-07-10 office-hours session and survived an
> adversarial spec review. **Do not silently re-litigate**; to reverse one, record a new
> decision here with rationale.

## <a name="adr-1"></a>ADR-1 — The Memory Engine is the architectural centerpiece

**Decision (builder's words):** "The core differentiator is a custom Memory Engine that
transforms raw memories into structured evidence, historical context, and reasoning for the
LLM. The LLM should remain replaceable and model-agnostic, while the Memory Engine becomes
the core of the application's intelligence."
**Trade-off:** more build than adopting a memory framework; in exchange, the differentiator
is owned code, and the "why not Mem0" question has a demonstrable answer.
**Rejected alternative:** Mem0 / Zep / Letta / LangMem as the memory layer — they model
memories as conversational facts and cannot compute quantitative-temporal answers
([06-retrieval-strategy.md](06-retrieval-strategy.md)). Independently reinforced by the
cross-model second opinion: "the 50% you must build is exactly your claimed differentiator."

## ADR-2 — Approach B-modified: two-tier engine + glass-box UI, spine-first

**Decision:** build Approach B (two-tier Memory Engine + glass-box UI) with the spine
(Approach A) as Milestone 1, so the project degrades gracefully to a complete minimal entry.
**Rejected:** A alone (looks like a competent RAG bot; weak creativity/production scores) and
C, OSS-library-first (see ADR-6).

## <a name="adr-3"></a>ADR-3 — Consolidation is event-driven, not scheduled

**Decision (builder's words):** "Instead of a scheduled nightly consolidation pipeline, I
want an event-driven Memory Engine that updates derived memories whenever new information is
ingested or when a relevant query requires fresh analysis... If additional components do not
materially improve the demo or judging score, they should be deferred."
**Trade-off:** loses the "thinks while you sleep" framing; gains live-on-camera insight
creation and zero scheduler infrastructure.
**Rejected alternative:** nightly Lambda consolidation job (proposed by the cross-model
second opinion; insight kept, infrastructure cut).

## ADR-4 — Seed data: reconstructed real history through the production pipeline

**Decision (builder's words):** "I plan to reconstruct my own real health history from the
past 6–12 months... The reconstruction will not invent facts; uncertain details will be
marked with confidence levels or estimated timestamps. These reconstructed events will then
be replayed through the same ingestion pipeline used for live user data."
**Consequences:** confidence + provenance became first-class schema columns; the demo video
is first-person; the causal story must be verified in real data before the demo script exists.
**Rejected alternatives:** fully synthetic persona (controllable but inauthentic); logging
real data from day 1 only (can't show year-scale memory); third-party importers (build cost,
uncontrollable insight presence). Raw SQL seeding is banned in all cases.

## <a name="adr-5"></a>ADR-5 — Graphiti as design donor, not runtime

**Decision:** adopt Graphiti's *concepts* (bi-temporal events: `event_time` vs `created_at`;
validity/invalidation semantics) into the CockroachDB schema; do **not** adopt its Neo4j
runtime. Reading time-boxed to one afternoon.
**Rationale:** a knowledge-graph runtime would gut the "custom engine on CockroachDB" thesis
and add an ops dependency; the typed-quantitative-event model is simpler and more honest for
this domain.

## <a name="adr-6"></a>ADR-6 — OSS library extraction deferred (build app-first)

**Decision:** keep the Memory Engine as a clean internal package; do not design a public
pip-installable API during the hackathon.
**Rationale:** judges score the app they can touch; generalizing before one domain works is
premature abstraction. The clean package boundary keeps post-hackathon extraction cheap.

## <a name="adr-7"></a>ADR-7 — Privacy: sanitized derivative for everything judge-facing

**Decision:** the public repo, hosted demo DB (the judge-facing surface, sandbox included),
replay dataset, and video contain only a **sanitized derivative** of the reconstructed
history — identifiers redacted/coarsened, sensitive blood values bucketed. Raw reconstruction
inputs stay local. The demo's causal story must survive sanitization.
**Origin:** adversarial spec review round 1, issue #1 — the strongest catch of the session.

## ADR-8 — UI grammar: conversation-first, memory-transparent (wireframe v3)

**Decision (builder's words):** "The goal is not to replace chat with a memory dashboard,
but to make the Memory Engine transparently enhance every conversation."
**Rejected alternatives:** v1 chat-dominant (memory reads as a sidebar afterthought) and v2
memory-dashboard-dominant (chat demoted). See [07-glass-box-ui.md](07-glass-box-ui.md).

## ADR-9 — Retraction never deletes

**Decision:** retracting a derived insight flips `status='retracted'`; supersession chains
via `superseded_by`. The engine's history of being wrong is itself memory (and demo
material). Mechanics beyond the status model: [OQ7](10-open-questions.md).

## ADR-10 — Tool-compliance posture: evidence three CockroachDB tools

**Decision:** Distributed Vector Indexing (runtime) + Managed MCP Server (dev-time,
evidenced via logged sessions + README) + ccloud CLI (provisioning scripts, screen-recorded).
MCP is explicitly **not** the runtime memory interface.
**Rationale:** the submission asks "what did the agent actually do with them?" — three
evidenced tools de-risk judge interpretation of dev-time MCP use.

## ADR-11 — Deploy-early

**Decision:** a minimal hosted deploy ships inside Milestone 1, with cost guards, so a
submittable URL exists from week 1 and first-deploy friction is paid early.
**Origin:** spec review round 1, issues #2/#9 (the graceful-degradation claim was false
without it).

## <a name="adr-12"></a>ADR-12 — Evidence traces are deterministic engine artifacts, not LLM output

**Decision (2026-07-11, builder-initiated during /plan-eng-review):** the Memory Engine
deterministically constructs an `EvidenceTrace` — evidence chain, provenance + confidence,
timeline slice, participating derived insights with lineage, executed queries, ranking
rationale — as a **byproduct of every context assembly** (and, in miniature, every
ingestion). The trace is persisted with the conversation turn and drives the entire
Glass-Box UI via the app API. The LLM generates natural language only; its citations are
**mechanically validated** against the trace after generation.

**Implemented as an internal engine capability returned with context assembly — explicitly
NOT an agent-exposed tool** (`build_evidence_trace(memory_ids)` was evaluated and rejected):

- A tool lets the model choose which memory IDs to disclose — reintroducing model
  discretion at exactly the point the glass box exists to eliminate.
- A tool call can be skipped by a buggy or token-pressured agent path; an assembly
  byproduct cannot — if context was assembled, a trace exists by construction.
- The trace is the *receipt* of assembly. Assembly already knows the executed queries,
  candidates, and ranking scores; reconstructing them later from bare IDs is lossy
  recomputation.
- The UI reads traces via `trace_id` through the app API — glass-box data never transits
  the model's output channel.

**Trade-off:** slightly larger assembly return type and a persistence obligation
(trace JSONB on the conversation turn) in exchange for a UI whose truthfulness is a
structural property rather than a model behavior.

**Rejected alternatives:** agent-exposed trace tool (above); LLM-generated explanations as
the transparency mechanism (the component being audited cannot be the auditor).

## <a name="adr-13"></a>ADR-13 — Engineering review decisions (2026-07-12, /plan-eng-review)

All locked interactively with the builder; each supersedes anything contradicting it in
earlier ADRs or docs.

1. **Consolidation executes synchronously in the ingestion request** with a hard time budget
   (~300ms); overflow defers to on-demand `analyze_series`. Retraction-condition evaluation
   rides the same pass. **Lambda is out of the runtime architecture** (AWS = Bedrock + S3 +
   the app host — ECS Express Mode since the 13.3 amendment). *(Rejected: async
   queue/worker — infra for a single-user-scale demo.)*
2. **Embeddings: Bedrock Titan Text Embeddings V2, 512-dim, normalized**; `VECTOR(512)`.
   CockroachDB's C-SPANN index is Euclidean-only; unit vectors make L2 ≡ cosine.
3. **Hosting: AWS App Runner**, single Docker image (FastAPI serving the built Vite/React
   SPA); deploy-early in Milestone 1. *(Rejected: ECS+ALB — setup cost without demo-visible
   benefit; Lambda+APIGW — cold starts + SSE friction.)*
   **Amended 2026-07-19 → Amazon ECS Express Mode.** App Runner stopped accepting new
   customers on 2026-04-30 (we had no service yet); AWS's recommended successor is ECS
   Express Mode, which removes exactly the setup cost the original rejection was about:
   one wizard/action provisions Fargate + a shared ALB + HTTPS URL + auto scaling. Same
   single image, same deploy-early property; CI deploys via the official
   `aws-actions/amazon-ecs-deploy-express-service` action ([../deploy.md](../deploy.md)).
   Budget shape changes (always-on Fargate task + ALB share instead of App Runner
   per-request idle) — folded into the T13 re-derivation.
4. **Pure production multi-user model** (builder's firm decision): standard SaaS accounts;
   every new user starts with empty memory; **no judge sandbox, no seed cloning, no
   sample-data onboarding**. The builder's account is a mature account bootstrapped through
   the production ingestion pipeline with real reconstructed history. **Accepted trade-off:**
   judges hands-on experience ingestion + retrieval over their own data; the deep-history
   money shot is witnessed via the builder's account (video/walkthrough), not driven by
   judges. This supersedes the sandbox language in earlier drafts and narrows ADR-7: the
   sanitized-derivative rule applies to the **repo-shipped replay dataset and video review**;
   the hosted production DB holds real user accounts behind auth.
5. **Ingestion failure policy (write-first as a guarantee, not a write order):** synchronous
   extraction; on success typed events are written directly (single transaction — no shadow
   note); on failure a `note` memory persists with a "saved — parsing incomplete" receipt and
   one inline retry; a later successful parse writes typed events and marks the note
   `superseded_by`. Embeddings nullable; backfill runs opportunistically on the user's next
   ingest plus a manual CLI command. **Input is never lost.**
6. **Pydantic payload registry** (`engine/types.py`): one model per memory type, typed hot
   fields, `extra="allow"` — validation at ingestion, no migrations for new attributes.
7. **Frontend: Vite + React SPA served by FastAPI**; monorepo `engine/ agent/ api/ web/ cli/`,
   one Dockerfile. *(Rejected: Next.js — second deploy surface; Streamlit — can't express the
   glass box.)*
8. **Tests run against real single-node CockroachDB Docker** (local + CI) with a day-one
   vector-index canary AND a day-one **LangGraph PostgresSaver-on-CockroachDB canary** (same
   risk class, same gate). Bedrock mocked behind the injected model interface.
   *Canary outcomes (2026-07-17):* vector canary green. PostgresSaver canary: **stock saver
   fails on CockroachDB** — its read query uses an unaliased set-returning function and 2-D
   `bytea` arrays (structurally rejected, cockroachdb #32552). The pre-agreed fallback
   landed far smaller than feared: `agent/checkpointer.py` `CockroachDBSaver`, a thin
   subclass rewriting only the read query (jsonb aggregates) + two loader overrides;
   `.setup()` migrations and all write paths run unmodified. Both canaries green against
   local single-node v26.2.4 AND the real CockroachDB Cloud cluster.
   *Engineering deep dive:* the complete investigation, compatibility analysis, debugging
   timeline, and implementation rationale are documented in
   [../engineering/cockroachdb-postgressaver.md](../engineering/cockroachdb-postgressaver.md)
   — the canonical reference for this layer; link there rather than re-explaining it.
9. **Evals:** extraction golden set (~30 cases, tolerance ranges) + citation-compliance set
   (~15 cases) — run against the **live model** (separate lane from mocked CI), manual
   trigger + pre-demo checklist.
10. **No replay clock — honest bi-temporality:** insights derived from reconstructed history
    keep truthful `created_at` (derived at replay) and are framed in **event-time** language
    ("this pattern emerged in your May–June data"). The "flagged the moment it happened" demo
    beat belongs to live ingestion, where it is provably true. *(Rejected: virtual clock in
    the production write path.)*
11. **Typed retraction conditions:** InsightPayload carries a structured
    `retraction_condition` object ({metric, comparator/direction, window_days, min_count});
    evaluated deterministically in the consolidation pass; prose is rendered from the object.
    *(Rejected: LLM-evaluated prose conditions — nondeterministic, budget-hostile.)*
12. **Analytics honesty:** consolidation output is a **labeled heuristic pattern flag** —
    daily bucketing with gaps left missing, `ruptures` PELT, bounded lag scan (7–35d) over
    whitelisted series pairs, documented "pattern strength" formula (effect size × coverage ×
    lag consistency). Never presented as probability or causal inference.
13. **Citation validation scope (honest claim):** mechanical validation guarantees citations
    resolve to real evidence in the turn's trace; **numeric/directional fidelity of prose is
    covered by the citation-compliance eval, not runtime validation**. Docs must not claim
    more.
14. **Conversation state:** LangGraph PostgresSaver checkpointer on CockroachDB holds graph
    execution state only; the app's own `turns` + `evidence_traces` tables (written in one
    transaction after a turn completes) are the **source of truth for UI rendering**.
15. **Auth: simple email+password sessions.** Production abuse/spend controls (rate limits,
    per-account budgets, email verification, spend kill-switch) are **explicitly out of scope
    this iteration** (builder decision; TODOS). Accepted residual risk: unbounded Bedrock
    spend under abuse during the public-URL window.

## Standing assumptions (verify, don't trust)

1. The builder's real history contains a demo-worthy causal story (Assignment verifies; a
   fallback story is chosen if not).
2. CockroachDB distributed vector indexing works on the affordable tier at 512 dims
   (Milestone 1, day one canary — permanent in CI).
3. Bedrock vision extraction is good enough for meal photos without fine-tuning (fallback:
   text-first logging remains fully functional).
4. Budget stays ≈ $50–100 for 40 days — **must be re-derived line-item** (Fargate + ALB share
   since the 13.3 amendment, previously App Runner idle
   cost, CockroachDB tier, replay Bedrock runs with extraction caching, evals) as a
   Milestone 1 task; abuse controls are out of scope (ADR-13.15), so this assumption also
   rests on no hostile traffic.
5. LangGraph PostgresSaver works on CockroachDB (day-one canary — same gate class as vector
   indexing; fallback is a thin hand-rolled checkpointer if the canary fails).
   **Resolved 2026-07-17:** stock saver fails; fallback landed as a thin read-path subclass
   (`agent/checkpointer.py`), canary green vs local and the real Cloud cluster (ADR-13.8).
