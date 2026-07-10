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

## Standing assumptions (verify, don't trust)

1. The builder's real history contains a demo-worthy causal story (Assignment verifies; a
   fallback story is chosen if not).
2. CockroachDB distributed vector indexing works on the affordable tier at the needed
   dimensionality (Milestone 1, day one).
3. Bedrock vision extraction is good enough for meal photos without fine-tuning (fallback:
   text-first logging remains fully functional).
4. Budget stays ≈ $50–100 for 40 days with request caps in place.
5. Judges can be given write access safely via sandbox isolation ([OQ3](10-open-questions.md)).
