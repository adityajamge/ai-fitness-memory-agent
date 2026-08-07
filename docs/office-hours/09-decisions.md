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
   **Amended 2026-08-03 (Phase 5 M0) — the stage is now specified, and the number is
   provisional.** "Synchronously in the ingestion request" was silent about *where* in the
   pipeline. It runs at **stage (F₀)**: after the turn's write transaction commits, after the
   receipt is built from committed rows, before the opportunistic embedding backfill — in its
   own transaction(s), best-effort, with created insights appended to the receipt. Pre-commit
   would let a *derived*-data failure lose the user's actual input, inverting never-lose-input;
   inside the transaction would violate transaction-boundaries rule 1 the moment an embedding
   is needed. A consolidation failure therefore **never fails a turn** (the same posture as
   backfill), because an insight lost to an error costs one re-derivation and nothing else.
   Insights are written with `embedding = NULL` and embedded by the existing T15 backfill,
   which removes a model call from the budget entirely.
   **The ~300ms figure predates the deployed topology and is provisional.**
   [../deploy.md](../deploy.md) records app in us-east-1 and CockroachDB Cloud in ap-south-1;
   a single round trip on that path is ~200–250ms and stage (F₀) needs 2–3. **T12 measures the
   real number and this item is re-derived from that measurement** — an honest amendment, not
   a silently relaxed constant. **First measurement (Phase 5 M3, 2026-08-04):** one series costs
   **~635 ms** on that path, so 300 ms completes exactly one series and defers the rest. The
   deferral mechanism is correct — nothing partial, no error, the turn undisturbed — and the
   number stands until T12 measures the deployed service rather than a developer machine. Rationale and the two structural defences (one consolidatable
   series per meal; no embed call on the hot path):
   [../engineering/consolidation-architecture.md §4.8](../engineering/consolidation-architecture.md).
2. **Embeddings: Bedrock Titan Text Embeddings V2, 512-dim, normalized**; `VECTOR(512)`.
   CockroachDB's C-SPANN index is Euclidean-only; unit vectors make L2 ≡ cosine.
   **Amended 2026-08-02 — provider selection is per role, not global.** The original design
   assumed one provider served every model call, expressed as a single `MODEL_PROVIDER`. That
   conflates two concerns which vary independently:

   | Role | Methods | Swappability |
   |---|---|---|
   | **LLM** | `extract_events`, `plan`, `narrate` | free — cost/quality/vendor availability |
   | **Embeddings** | `embed` | **effectively a one-way door** once memories exist: vectors from different models occupy different spaces, so changing it means re-embedding every row |

   Forced by evidence, not preference: on the builder's AWS account Bedrock serves Titan but
   **refuses `us.anthropic.claude-sonnet-4`** (`ResourceNotFoundException` — "marked by
   provider as Legacy"). So `MODEL_PROVIDER=bedrock` cannot do `plan`/`narrate`, while
   `claude_api` cannot `embed` — **no single value satisfies the app**. Selection is now
   `LLM_PROVIDER` / `EMBEDDING_PROVIDER`, each falling back to `MODEL_PROVIDER` then the
   default, composed by `CompositeProvider` (`agent/providers/__init__.py`). Titan V2 / 512 /
   normalized remains the embedding decision; only *who may serve the other role* changed.
   **This strengthens rather than weakens [ADR-1](#adr-1)** — "the LLM should remain
   replaceable" is now true of the LLM specifically, instead of being coupled to the embedder.
   `claude_api` is consequently no longer a development-only adapter: paired with a Bedrock
   embedding role it is a supported production configuration for the LLM role. Backward
   compatible — a `MODEL_PROVIDER`-only config resolves both roles to it and is returned
   **unwrapped**, so existing deployments and tests are untouched.
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
    **Refined 2026-08-03 (Phase 5 M0) — the schema is pinned and "counterexample" is defined.**
    "comparator/direction" was ambiguous about whether one field or two were meant, and the
    canonical example ("retract if 3+ counterexamples in rolling 30d") never said what a
    counterexample *is* — both are load-bearing for a *deterministic* evaluator, which is the
    entire point of typing the condition. Pinned as
    `{metric, direction: 'rising'|'falling', threshold: float|None, window_days, min_count}`:
    the comparator exists as an optional `threshold` when there is something to compare
    against, and `direction` alone otherwise. An observation of `metric` inside the trailing
    `window_days` is a **counterexample** when — with no threshold — it moves in `direction`
    relative to the insight's post-shift level (`level_shift`) or its later measurement
    (`intervention_outcome`), or — with a threshold — it crosses that threshold in
    `direction`. `count ≥ min_count` flips `status='retracted'`; ADR-9 still holds, nothing is
    ever deleted or rewritten. Full spec:
    [../engineering/consolidation-architecture.md §4.14](../engineering/consolidation-architecture.md).
12. **Analytics honesty:** consolidation output is a **labeled heuristic pattern flag** —
    daily bucketing with gaps left missing, `ruptures` PELT, bounded lag scan (7–35d) over
    whitelisted series pairs, documented "pattern strength" formula (effect size × coverage ×
    lag consistency). Never presented as probability or causal inference.
    **Amended 2026-08-03 (Phase 5 M0) — the detector set is replaced; the honesty posture is
    not.** Measured against the data the Phase 4 replay actually committed, the two named
    algorithms have nothing they can honestly run on. The account's only daily metric series
    (`protein_g`) is a **four-level step function with zero within-segment variance**, written
    by the converter's period expansion from three reviewed payload-table entries — PELT over
    it rediscovers the converter's own boundaries at infinite effect size and would publish a
    near-perfect pattern strength for an artifact, in the panel the glass box invites judges to
    click into. And the bounded lag scan has **no valid pair**: workout metrics are `null` by
    design, supplement doses conflate products, and sleep and weight have zero rows. Both are
    therefore **removed rather than kept as unexercised infrastructure**, the same call
    [ADR-15.2](#adr-15) made for the replay extraction cache, with the same discipline of
    recording a re-add trigger and recipe (consolidation-architecture.md §10). `ruptures` is
    consequently **not a dependency of this project**.
    Two deterministic detectors replace them, matched to how health data is actually generated
    (sparse outcome measurements, dense behavioural change): **`level_shift`** — a metric's
    level moves between adjacent observation segments — and **`intervention_outcome`** — a
    sparsely-measured marker changes between two measurements, with the structurally-detected
    changes inside that interval reported as its lineage. Interventions are detected
    **structurally** (series onset, or a level shift in a behavioural series), never by reading
    note prose: the engine still never interprets language.
    Two consequences for this item's own wording: consolidation observes **assertions, not
    materialized period days** (an expanded period collapses to one observation, so the
    `expanded_from` honesty signal of [ADR-15.4](#adr-15) is read rather than discarded), and
    the pattern-strength formula's third factor is renamed **lag consistency → specificity**
    (`1 / concurrent changes`), which generalizes it once no lag detector exists and is what
    keeps `intervention_outcome` from reading as causal. **Unchanged:** daily bucketing with
    gaps left missing (never interpolated), the documented formula published *with its three
    components*, and "labeled hypothesis, never probability or causal inference" — plus a new
    minimum-evidence rule under which a detector that cannot clear its thresholds **emits
    nothing**. **Further amended 2026-08-04:** the *effect* factor is measured against a
    **per-series scale in the metric's own units**, not as a relative change against the
    baseline. Implementation showed a single relative floor cannot serve both a marker that
    moves multiples (vitamin D 6.2 → 38.4) and a physiologically bounded quantity — it refused
    every clinically meaningful body-composition and weight change outright. Each consolidatable
    series now declares `min_delta` (the noise floor, which gates) and `full_delta` (a full-size
    change, which is `effect`'s denominator); these are **product heuristics with a stated
    basis, never clinical thresholds**, which is the same honesty line that keeps a pattern
    strength from being called a probability. Full rationale and rejected alternatives:
    [../engineering/consolidation-architecture.md §4.1–§4.3, §4.13, §4.15](../engineering/consolidation-architecture.md).
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

## <a name="adr-14"></a>ADR-14 — Phase 3 decisions (agent spine & read path, 2026-07-22 → 2026-07-24)

Decisions taken **during** Phase 3 implementation (M1–M6) and now implemented in code. They
were held in a "Temporary Architecture Decision Log" in `TODOS.md` while these docs were
frozen; this ADR is their permanent home. Each refines, rather than reverses, an earlier
decision — where one amends ADR-12 or ADR-13 that is stated explicitly.

### 14.1 Routing is tool selection — one planner surface, not ROUTE + PLAN

`ModelProvider` gained a single `plan(question, tools, *, now, tz) -> list[ToolCall]`
surface. There is **no** separate `route()`/`classify()` method: with `log_memory` among the
offered tools, the planner selecting it *is* the ingest classification, selecting retrieval
tools is a query turn, selecting both is a "both" turn.

**Why:** classifying the turn and choosing tools are one cognitive act over one vocabulary —
a separate ROUTE step would read the same sentence twice. It is also the native shape of
tool-use models (`toolChoice: auto` already returns "which tools, with which arguments, or
none"), so splitting it doubles latency and token cost for a decision the model was already
making. And it removes a class of failure: no intent enum to keep in sync with the tool set,
no ROUTE/PLAN disagreement to reconcile.

**Why this preserves the architecture:** 05's load-bearing invariant is the *query-planning
boundary* — exactly one place understands natural language, everything below the tool-call
boundary is deterministic. Merging keeps that exactly: still one NL step, still only typed
tool calls out, still deterministic downstream. 05's two-node drawing was labelled
"design-level" — it described logical stages, not a two-model-call contract.
*(Amends the graph shape in [05](05-agent-architecture.md).)*

### 14.2 The empty plan is an assertion, not a failure

`plan()` returning `[]` is a **positive assertion** that the turn needs no memory operation
(small talk, a greeting), distinct from `PlanningError` (the call failed or was malformed).
Mirrors the extraction empty-result contract (ADR-13.5): the planner's analogue of "nothing
to log". Gives the graph a clean, non-erroring conversational path instead of forcing a
retrieval or crashing a turn.

### 14.3 Ingest runs before retrieve within a turn

On a "both" turn the graph runs ingestion **first**, so a memory logged this turn is already
committed when the same turn's aggregation scans for it ("logged my run — am I improving?").
Without the ordering the answer would silently omit the event the user just reported — a
wrong answer that looks right, the worst failure class for a glass box.

### 14.4 The closed builder set gains `lookup_events` and `count_events`

[06](06-retrieval-strategy.md) enumerated aggregation, last/first-event lookup, timeline
slice, vector K-NN, and insight lookup. Implementation splits the point-lookup family into
**`lookup_events`** (newest/oldest of a type, optional exact item containment) and adds
**`count_events`** (how many events of a type in a range). The addition exists because the
aggregation family's `count` was scoped to rows *where the metric is present* — counting
logged protein values is a different question from counting meals, and overloading one
`count` would have made both ambiguous. The set remains closed and parameterized.

### 14.5 Ranking recency is relative to the retrieved set

06 specified "temporal proximity **to the question's window**". Assembly instead normalizes
recency *within the candidate set* (newest retrieved → 1.0, oldest → 0.0).

**Why the deviation:** using the question's window would require assembly to know that
window — i.e. parse the question (breaking the no-language determinism boundary) or reconcile
each tool's `date_range` into one reference window (undefined when tools disagree, absent for
recall entirely). Within-set normalization is deterministic, language-free, and sufficient
for ordering *within* one answer, which is all ranking is for.

### 14.6 Two views of the evidence: the trace shows more than the model saw

`EvidenceTrace.evidence` carries **everything retrieved** (deduped across tools, ranked);
`ContextBlock.memories` carries only the diversity-capped, budget-limited subset handed to
the narrator, with `EvidenceTrace.ranking` explaining what was cut and why.

**Why:** 06 specifies a budgeted context block and 03 a trace of "memory IDs used", but
neither says whether truncation applies to both. Keeping the trace complete makes "why the
engine picked these and dropped those" inspectable, and keeps the token budget a *narration*
concern rather than an evidence-hiding one.

### 14.7 Assembly is a pure function; aggregate rows are hydrated in Phase 6

`assemble()` touches no database. For an aggregate's contributing memory IDs it therefore has
the IDs but not their snapshot metadata, and does not fetch it — hydrating those rows is
exactly T16's batch-fetch (Phase 6). This keeps assembly fixture-testable and deterministic,
and is the mechanism behind 14.8.

### 14.8 The citable surface is `citable_ids`, not `trace.evidence` alone — **open item for T7**

ADR-12/13.13 say the narrator may cite only IDs "present in the turn's EvidenceTrace". Given
14.7, an aggregate's contributing IDs live in `ContextBlock.aggregates[].buckets[]
.evidence_ids` (exposed as `ContextBlock.citable_ids()`) and are **not** in `trace.evidence`.
A *valid* citation of an aggregated meal would therefore be flagged invalid by a validator
that reads `trace.evidence` alone.

**T7 must resolve this before building citation validation** — either validate against the
full citable set (`trace.evidence` ∪ aggregate/count contributing IDs), or carry those IDs
into the persisted trace so "the UI reads the trace" stays literally true. **Recommended: the
latter**, keeping the trace the single source of glass-box truth. *(Refines
[ADR-12](#adr-12); tracked on T7 in [11](11-implementation-tasks.md).)*

### 14.9 Graph-state durability boundary, and where it is enforced

ADR-13.14 says the checkpointer holds execution state while `turns`/`evidence_traces` are UI
truth. Phase 3 makes that boundary **enforceable rather than conventional**: heavyweight
turn-local objects (`ContextBlock`, `EvidenceTrace`, `RetrievalOutcome`, `Receipt`) must
never enter checkpointed state. Checkpointed channels are small and serde-safe
(`messages`, `user_id`, `question`, `now`, `tz`, `tool_calls`, `answer`, `citations`); the
heavy objects ride a per-invocation carrier passed through `RunnableConfig`.

Enforcement is layered, and the layers are **not** equivalent: the **guard on
`CockroachDBSaver`'s serialization path is the architectural guarantee** — every persist in
tests and production flows through it, so the invariant cannot be sidestepped by editing the
state schema. A node-output wrapper supplies the developer-facing signal, and an allowlist
tripwire test makes adding a channel a conscious act. Full investigation, including why
LangGraph's own behavior could not be relied on:
[../engineering/graph-state-durability.md](../engineering/graph-state-durability.md).
*(Refines ADR-13.14.)*

### 14.10 Timezone is engine-injected; it is never a planner slot

No retrieval tool exposes a `tz` argument. The timezone is a property of the *user*, not of
the question, so the tool layer injects it. Letting a model fill it would put language
interpretation where determinism belongs and make bucket boundaries model-dependent.

### 14.11 Slot validation is strict — no invented defaults

A missing required slot (e.g. an aggregation with no date range) is rejected, never defaulted
to a guessed window: inventing a range answers a question the user did not ask. Likewise an
explicitly empty `types` filter is rejected rather than silently reinterpreted as "no
filter". Every planner mistake dies above the database, before any SQL is composed.

### 14.12 Honest degradation: a failed tool costs that tool, not the turn

An invalid tool call, or an embedding failure that prevents semantic recall, is recorded and
surfaced to the caller while the remaining retrieval still runs and the turn still answers.
Silently dropping a requested retrieval would produce a confidently incomplete answer; failing
the whole turn would lose work the engine could legitimately do. The same posture as
never-lose-input (ADR-13.5), applied to the read path.

### 14.13 Thread identity is namespaced by user

The client's `thread_id` is opaque and is namespaced with the caller's `user_id` before it
reaches the checkpointer, so two users presenting the same string get two different threads.
Replaying another user's thread id is therefore not a way to read their conversation — it
silently starts your own, the same "existence is not probeable" posture as
`GET /api/memories/{id}` (ADR-13.4).

### 14.14 The trace rides inline in Phase 3, by `trace_id` from Phase 6

`POST /api/chat` returns `{thread_id, answer, citations, receipts, trace, errors}` with the
`EvidenceTrace` **inline**. Persistence (`evidence_traces`) and `trace_id` fetch are T7; the
response shape is designed so Phase 6 *adds* fields rather than reshaping. ADR-12's property
("a trace exists whenever context was assembled") already holds — only its storage is
deferred.

## <a name="adr-15"></a>ADR-15 — Phase 4 decisions (history bootstrap / replay, 2026-07-29 → 2026-08-02)

Decisions taken during Phase 4 and **now proven by a production run**: 424 records of the
builder's real reconstructed history replayed into the live account, 0 failures, 0 NULL
embeddings, idempotent on rerun. They were held in
[engineering/replay-architecture.md](../engineering/replay-architecture.md) §4 while Phase 4 was
in flight; that document remains canonical for the *how* (dataset format, expansion rules, CLI
contract, failure artifact). This ADR records only what is architecturally binding, and only
what implementation and M5 actually validated.

**OQ5 is resolved: GO.** The money question is answerable from the database — Vitamin D
6.20 (2026-03-25) → 38.4 (2026-07-03) returns through `lookup_events`, with the causal chain
(supplement start, dose reduction) reachable by semantic recall. Story C, the fallback narrative,
is not needed.

### 15.1 Replay is the production write path used as a batch client — no import feature

Replay reuses `IngestionService` end-to-end through a second entry point (`ingest_events`), which
skips extraction and shares validation, embedding, the single write transaction, receipts, and
backfill with `ingest_text`. No parallel pipeline, no bulk-insert path, no `external_ref` column.

**Why:** a second write path would need its own proof of the never-lose-input and
transaction-boundary guarantees. One shared tail keeps those one testable property.
**Invariant:** one record = one transaction, row-at-a-time — never batch inserts to speed a bulk
run (that reintroduces the C-SPANN footgun the T1 canary guards). **Tradeoff:** ~1.01 s/record
and no parallelism; irrelevant at 424 records, and it left the write path's guarantees untouched.
This also means [ADR-13.4](#adr-13)'s "every account starts empty" is preserved — replay is a
**dev-time operator tool**, not a user-facing import.

### 15.2 Zero runtime inference: structuring happens at dev time

Every replay record reaches the database already typed. Turning an unstructured personal history
into typed events is inference, and Phase 4 performs it at **development** time (LLM-assisted,
human-reviewed into a reviewed payload table) rather than at replay time — the same dev-time /
runtime split [ADR-10](#adr-10) locked for the MCP server.

**Why:** the values replay commits are the ones the demo verifies (the Vitamin D pair). Re-parsing
them through a model at runtime risks a transcription error landing precisely there. The cost
argument (re-runs are free) is real but secondary — this is a correctness decision.
**Invariant, mechanically checked:** a replay run makes **zero** `extract_events` calls; a
property test asserts it, and the M5 run confirmed it. Consequently the originally-planned
extraction cache was **deleted rather than kept as unexercised infrastructure**; its re-add
trigger is recorded in replay-architecture.md §8. **Tradeoff:** replay cannot ingest anything the
converter cannot type — by design, since a validation failure on this path means bad *input*, and
is fatal rather than silently degraded to a note.

### 15.3 Idempotency lives in the CLI, keyed on converter-owned ids

The engine keeps its no-deduplication behavior (correct for live chat). All replay protection is
a local append-only ledger keyed on a `record_id` derived from the record's **stable coordinates**
(`source_ref` + occurrence), never its content, written **strictly after** the ingest call returns.

**Why content-keying was rejected:** it would make an ordinary markdown edit produce a new key for
an already-committed record, re-ingesting it — the P0 duplicate path reachable through a
documentation change. **Why post-commit ordering:** the reverse marks work that may never commit
and skips it forever. **Invariant:** ledger writes strictly after commit; ids are converter-owned
and machine-verified, never hand-written. **Accepted, bounded residual:** a crash between commit
and ledger write re-processes exactly one record — pinned by test, not eliminated. **Proven:**
rerunning the full production command reported 0 new / 424 skipped in 2.7s with zero duplicate
rows.

### 15.4 Periods expand into dated occurrences, and must say so

`memories.event_time` is a single timestamp, so period facts ("4 eggs daily, March–April") are
resolved at conversion time into one record per occurrence — but only when the assertion carries a
per-occurrence quantity, at **its own cadence**, clamped at the live-logging cutover.

**Why cadence fidelity is not a detail:** expanding a *weekly* 60,000 IU dose daily would assert
7× the real dose — fabricated data in the table the glass box invites judges to click into. The
real dataset produced 13 weekly records, not 88. **The honesty mechanism is two signals, not one**:
lowered `confidence` **and** an inline `expanded_from` marker naming the parent assertion and its
bounds. Shipping only the first is an ADR-4 violation, because nothing then distinguishes a
materialized day from an observed event — this happened in the first production run and was
repaired by metadata backfill (replay-architecture.md §4.1). **Tradeoff, acknowledged:** synthetic
daily rows are a **replay-scoped tactic**, not the architecture's opinion on periods. Storing one
row per period and materializing occurrences during aggregation is the better design; it is
deferred to Phase 5 on scope, not merit, and is recorded as a handoff.

### 15.5 Corrections propagate by supersession, never by rewrite, and never automatically

Stable ids prevent duplicates *and* silently prevent corrections: editing a fact leaves the id
unchanged, so a re-run skips it. Drift is therefore detected by comparing a stored content hash,
reported as a field-level diff on **every** run, and applied only under an explicit
`--apply-corrections` — which inserts replacements and `mark_superseded`s the prior rows in one
transaction ([ADR-9](#adr-9): retraction never deletes).

**Why the flag:** if the converter ever drifts subtly (float formatting, whitespace), automatic
behavior would mass-supersede the whole dataset in one run. Detection is free and always on; the
wide-blast-radius action requires intent. **Status, stated honestly:** implemented and tested, but
**not exercised by M5** — the production run had no corrections. The first real correction will be
the true test.

### 15.6 What Phase 4 taught: seams, not components, were where defects lived

Both defects the milestone found sat **between** milestones whose sides were each individually
correct and tested — the M2 id guard not knowing a third id shape M1 legitimately produced, and
the M1→M3 adapter dropping a field neither side required. Neither raised an error; both would have
shipped a run reporting success. **Consequence for later phases:** a green summary line is not
evidence of success, and integration seams need their own assertions at the *outermost* observable
layer (the committed row), not only at the unit that owns the logic. The M5 smoke step — a
10-record rehearsal into a throwaway account — is what caught the first and is retained as
standard practice for any future bulk operation.

## ADR-16 — Phase 5 (insight engine) — *accepted 2026-08-06*

Promoted from [../engineering/consolidation-architecture.md §4](../engineering/consolidation-architecture.md)
(LOCKED 2026-08-03), which remains canonical for the *how* — its §11 implementation record and
§5 invariant table are not duplicated here. This records only what is architecturally binding
and what implementation actually validated. Amendments to ADR-13.1, 13.11, and 13.12 are
recorded inline above.

**16.1 Two detectors, chosen against the data — not the design.** The planned analytics
(`ruptures` PELT changepoints + a 7–35d lag scan) were measured against what the Phase 4 replay
actually committed and had nothing to run on. They are replaced by two deterministic detectors,
`level_shift` and `intervention_outcome`. `ruptures` is not a dependency. *Validated:* both
detectors produce insights over the real replayed history via the M5d sweep.

**16.2 No detector reads text.** Every intervention reduces to exact equality or arithmetic
(I-8). The notes in the history look enough like structured data that this is the invariant most
likely to be eroded by a well-meaning improvement.

**16.3 `pattern_strength` is a labeled heuristic, never a probability.** It is exactly
`effect × coverage × specificity`, and the three components stay separately visible. Enforced at
the type layer: `InsightPayload` rejects a `pattern_strength` that does not match the product
within `1e-6` (I-19).

**16.4 Per-series `EffectScale`, not one global threshold.** A single relative effect floor
refused every clinically meaningful body-composition change (body fat 39.2 → 36.0 is 8.2%). Each
series carries its own `min_delta` gate and `full_delta` denominator. *These are product
heuristics, not clinical thresholds*, and the code says so at each calibration site.

**16.5 Insight identity is a fingerprint, and supersession is how it changes.** Identity is
`(user_id, kind, series_metric)` plus a fingerprint over `claim_dates` + values + intervention
ids. `claim_dates` — not the evidence window — because fingerprinting the window made an
unchanged claim supersede itself once per logged day.

**16.6 Retraction only ever sets `status='retracted'`.** It never deletes or rewrites a payload
(I-20) and is fully deterministic: no model calls, no language parsing, no note interpretation
(I-21). Direction is judged against typed `pre_value`/`post_value` fields, not prose.

**16.7 Consolidation runs at stage (F₀): post-commit, best-effort, budgeted.** Outside the
ingestion transaction, after the receipt. Insights are derived data — losing one costs a
re-derivation, while moving it inside the turn transaction would put every write at risk to
protect a value that can be recomputed. It swallows its own exceptions by construction.

**16.8 Writes are graph-dispatched; the retrieval builder set stays read-only.** `analyze_series`
joins `log_memory` as a write tool routed through the graph's `_STAGES` table (I-17). `assemble()`
stays pure.

**16.9 Freshness is derived, never stored.** No `last_evaluated_at` column: it would reintroduce
payload mutation for a derivable value and give one fact two sources of truth.

**16.10 One `ConsolidationService`, one composition root.** The API builds it once and shares it
between the ingestion tail and the graph node; `cli/consolidate.py` uses the same service and
owns no consolidation logic of its own.

### Open — do not treat as settled

**The consolidation budget number.** ADR-13.1's ~300 ms predates the deployed topology and
remains **provisional**. Measured during M5: consolidation costs **~635 ms per series** from the
`us-east-1` app to the `ap-south-1` cluster, so a 300 ms budget completes exactly one series and
cleanly defers the rest. The *mechanism* is validated — clean deferral, nothing partial, no
error — and the *number* is T12's to re-derive. **M6/T12 is postponed, not waived**
([TODOS.md](../../TODOS.md)); this field stays open until a production measurement exists, and
open question Q3 (does the deferral path need a catch-up trigger?) rides with it.

**Q1 — insight citability.** `citable_ids()` includes each participating insight's own id but
**not** its `evidence_ids`. Whether the narrator may cite through an insight to its underlying
memories is T7's call alongside ADR-14.8, and is asserted by test so that widening the surface
must be deliberate. A citable surface is far easier to widen later than to narrow.

### Cut

**M7 — photo ingestion** (S3 + Bedrock vision) was the designated first-to-cut milestone and was
cut on 2026-08-06. Invariant **I-23** (a photo turn persists something for every outcome) stands
in §5 as unimplemented, not withdrawn. Remaining work is in [TODOS.md](../../TODOS.md).

## Standing assumptions (verify, don't trust)

1. The builder's real history contains a demo-worthy causal story (Assignment verifies; a
   fallback story is chosen if not).
2. CockroachDB distributed vector indexing works on the affordable tier at 512 dims
   (Milestone 1, day one canary — permanent in CI).
3. Bedrock vision extraction is good enough for meal photos without fine-tuning (fallback:
   text-first logging remains fully functional).
4. Budget stays ≈ $50–100 for 40 days — **must be re-derived line-item** (Fargate + ALB share
   since the 13.3 amendment, previously App Runner idle
   cost, CockroachDB tier, replay Bedrock runs, evals) as a
   Milestone 1 task; abuse controls are out of scope (ADR-13.15), so this assumption also
   rests on no hostile traffic.
   **Replay's share resolved 2026-08-02 ([ADR-15.2](#adr-15)):** replay makes zero extraction
   calls, so the line item is 424 Titan embedding calls (well under $0.01) rather than the
   originally-budgeted extraction runs. The extraction cache that assumption referenced no
   longer exists.
5. LangGraph PostgresSaver works on CockroachDB (day-one canary — same gate class as vector
   indexing; fallback is a thin hand-rolled checkpointer if the canary fails).
   **Resolved 2026-07-17:** stock saver fails; fallback landed as a thin read-path subclass
   (`agent/checkpointer.py`), canary green vs local and the real Cloud cluster (ADR-13.8).
