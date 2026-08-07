# Glass-Box Architecture (Phase 6 / T7 · T11 · T16)

> **Status: LOCKED 2026-08-06** for the duration of Phase 6, the same holding pattern
> [replay-architecture.md](replay-architecture.md) used for Phase 4 and
> [consolidation-architecture.md](consolidation-architecture.md) for Phase 5. §4 is canonical
> for *how* Phase 6 works; it is promoted into **ADR-17** at phase close.
>
> Backlog: [T7 / T11 / T16](../office-hours/11-implementation-tasks.md).
> Philosophy and build order: [07-glass-box-ui.md](../office-hours/07-glass-box-ui.md).

---

## 1. Purpose

Phase 5 made the memory think. Every artifact it produces — insights, lineage, retraction,
deterministic `EvidenceTrace`s — is currently **invisible**: emitted, tested, and then dropped
on the floor at the end of the turn. Phase 6 persists those artifacts, serves them, and renders
them.

The thesis this phase defends is narrow and worth stating plainly: *a judge should be able to
click a claim and reach the rows and the SQL that produced it.* Everything below is in service
of making that mechanically true rather than rhetorically true.

## 2. Scope

**In:** trace persistence (T7), citation validation (T7), the glass-box read API and batch
hydration (T16), empty states (T11), and the SPA that renders all of it.

**Out:** photo ingestion (cut, [TODOS.md](../../TODOS.md)); the production latency profile
(postponed, same file); anything that would re-open a Phase 5 decision.

## 3. Where the trace comes from today

One fact drives most of §4. `assemble()` emits the `ContextBlock` and the `EvidenceTrace`
**as a pair**, at `assemble_node` ([agent/graph.py:274-279](../../agent/graph.py#L274-L279)) —
after ingestion has already committed, and *before* narration has produced an answer. The turn
therefore looks like this:

```
plan ──► [ingest ──► consolidate] ──► retrieve ──► assemble ──► narrate ──► END
             │            │                           │            │
     memories COMMIT   stage (F₀)                trace EXISTS   answer EXISTS
      (transaction D)  post-commit                  here          here
```

Three consequences that §4 has to live with:

1. The memories' transaction has **already committed** by the time a trace exists.
2. The answer — and therefore the citations to validate — does not exist until *after* the
   trace does.
3. `assemble()` is pure (ADR-14.7). It has aggregate contributing IDs but not their snapshot
   metadata, and does not fetch it.

---

## 4. Decisions

### 4.1 Q1 — insight `evidence_ids` are rendered, never cited *(resolves Q1)*

**Decision.** `citable_ids()` is **unchanged**. The narrator may cite a memory, an aggregate or
count contributor, and an insight's **own id** — never the `evidence_ids` underneath an insight.

The rule this makes explicit, and which every future change to the citable surface must satisfy:

> **The citable set is what the narrator can be held to.** A memory ID is citable when its
> relationship to the narrated claim is *mechanically verifiable*: the row was shown as
> evidence, or it arithmetically constitutes a stated aggregate. **Lineage — the deterministic
> support behind a derived claim — is rendered, not cited.**

**Why not widen.** The tempting argument is consistency: an aggregate's contributing IDs are
citable, and an insight looks structurally identical — a derived fact with deterministic
support. The relationships differ in kind:

| | Aggregate → contributors | Insight → `evidence_ids` |
|---|---|---|
| Relationship | arithmetic, **total** | inferential, **partial** (capped at `MAX_EVIDENCE_IDS = 24`) |
| "This row supports the claim" | true by construction | a judgement the *detector* made |
| Narrator saw the content? | it saw the derived number the rows constitute | no — bare IDs, no snapshot metadata (ADR-14.7) |

Citing through an insight would have the narrator assert an inferential link on the detector's
behalf, about content it never saw. That is the class of claim ADR-12 exists to prevent.

**Reversibility is the decisive argument.** Narrow → wide is a non-breaking change. Wide →
narrow turns previously-valid answers into flagged ones. Phase 5 chose narrow and left a test
asserting it so that widening must be deliberate; that test stands.

**Nothing is hidden from the user.** An insight chip resolves to a panel showing the hypothesis,
its three pattern-strength components, and its lineage rows. The user sees everything; the model
just does not get to claim it.

**Obligation this creates:** the narrator prompt must state that insight `evidence_ids` are not
citable. Without it the model will cite them and the validator will flag correct-looking
answers — a prompt defect that presents as a validator bug.

**Rejected:** conditional widening (cite through only when the insight is the sentence's sole
support) — it makes the citable set depend on sentence semantics, so the validator would have to
parse prose, which is precisely what ADR-12 forbids.

### 4.2 ADR-14.8 — the trace carries its own citable set *(resolves ADR-14.8's open item)*

**Decision.** `EvidenceTrace` gains a `citable_ids` field, persisted in the trace JSON. Adopts
ADR-14.8's own recommendation.

**Why.** ADR-14.8 records a live defect: aggregate contributing IDs live in `ContextBlock`, not
in `trace.evidence`, so a *valid* citation of an aggregated meal would be flagged **invalid** by
a validator reading the trace alone. Carrying the set into the trace makes "the UI reads the
trace" literally true and gives the validator exactly one source. This must land in M1, before
the validator in M2, or M2 ships with a known false-positive class.

**Consequence.** The trace becomes self-contained: given a persisted trace and nothing else, a
reader can decide whether any citation in the answer was legitimate.

### 4.3 Turn and trace persist at stage (G) — after narrate, in one transaction

**Decision.** The `turns` row and its `evidence_traces` row are written **together, in a single
transaction, after narration** — a new stage **(G)**, outside the ingestion transaction.

**This amends [ingestion-transaction-boundaries.md §12](ingestion-transaction-boundaries.md) and
the phrasing of ADR-13.14**, both of which anticipated the trace joining the memories'
transaction (D)/(D'). That is not implementable, and would be wrong if it were:

- **Not implementable.** The trace does not exist when (D) commits — it is produced two nodes
  later — and the citations do not exist until after narration. Honouring the literal rule would
  mean holding (D) open across `assemble` *and* `narrate`.
- **Wrong if it were.** That means holding the transaction that guarantees never-lose-input open
  across a multi-second LLM call. This project has already paid for a long-running write
  transaction once: a 21½-minute `DELETE` that manifested as `RETRY_SERIALIZABLE`
  ([cockroachdb-lessons-learned.md](cockroachdb-lessons-learned.md) Part I). Putting narration
  inside (D) is the same mistake with a worse blast radius.
- **The precedent already exists.** Stage (F₀) sits outside (D) for exactly this reasoning:
  derived data, best-effort, never widen the atomic turn to cover it. The trace is derived data
  by the same test — losing one costs the glass box for that turn, and costs the user nothing,
  because the memories are already durably committed and the answer was already delivered.

**What (G) guarantees, precisely:**

| | Guarantee |
|---|---|
| Turn ↔ trace | **Atomic with each other.** Never a turn without its trace, never an orphan trace. |
| Turn ↔ memories | **Not atomic.** Memories commit first, at (D); (G) references them by id. |
| (G) fails | The turn's memories and answer stand. The glass box loses that turn. Logged, never silent. |

**Invariant boundary (I-11 unchanged):** (G) is not a fourth ingestion transaction shape — it
writes `turns`/`evidence_traces`, never `memories`, and never runs on the direct-ingest path.

**Rejected:** reordering the graph so assembly precedes ingestion (breaks M5c's same-turn
insight visibility and Phase 5's ordering guarantee); two separate transactions for turn and
trace (admits orphan rows for no benefit); best-effort like (F₀) with no atomicity between the
two rows (the UI's first read would have to tolerate a turn whose trace never arrives).

### 4.4 Citation validation is mechanical, and its scope is stated

**Decision.** Validation extracts `[uuid]` markers from the answer, resolves each against the
trace's `citable_ids`, and classifies the answer. It **never** parses prose for meaning, never
calls a model, and never rewrites the answer.

Three outcomes, mirroring the extraction contract's shape:

| Outcome | Meaning |
|---|---|
| `valid` | every marker resolves |
| `invalid` | at least one marker does not resolve — surfaced to the UI, flagged in place |
| `uncited` | the answer makes claims with no markers at all |

**Honest scope (ADR-13.13).** This proves a cited ID *was retrieved for this turn*. It does
**not** prove the sentence accurately characterises that row — numeric and directional fidelity
is the citation eval's job, not the validator's. Phase 7 documentation must not overstate this,
and the UI must not imply "verified" when it means "resolvable".

**A fourth case, added in M2: no markers and nothing citable is `valid`, not `uncited`.**
"I don't have anything logged for that window yet" is the *correct* answer to an empty context,
and it is the single most common thing a brand-new account sees. Classifying it as a citation
defect would make the UI cry wolf on every judge's first question. `uncited` is therefore
reserved for the genuinely suspicious case: evidence was retrieved and the answer cited none of
it. `CitationReport.citable_count` carries the distinction so the UI never has to inspect prose
to make it.

**The report is not persisted, and does not need to be (M2 decision).** It is a pure function
of two things that *are* persisted — the answer on the `turns` row and `citable_ids` in the
trace — so M3 recomputes it on read and gets a bit-identical verdict. This does not conflict
with I-29: that invariant forbids re-deriving the **trace**, whose content depends on query
results that no longer exist at read time. The report has no such dependency. Persisting it
would add a second source of truth for a value that cannot drift from its inputs.

### 4.5 The read API is thin, and the trace is served verbatim

**Decision.** Glass-box endpoints are read-only, user-scoped, and return the persisted trace
**as stored** — no re-derivation, no recomputation, no merging.

Re-deriving a trace at read time would let the rendered glass box drift from what the turn
actually did, which defeats the entire point. The stored JSONB is the artifact.

**Cross-user denial is a route-level invariant**, tested per endpoint (I-28), not left to a
`WHERE` clause a future refactor could drop.

### 4.6 Hydration is batched, and it is the only place the API joins rows

**Decision.** T16's batch fetch resolves a set of memory IDs to display rows in **one** query.
The UI never issues N requests for N chips.

This is where ADR-14.7's deferred work lands: aggregates carry contributing IDs without snapshot
metadata, and the UI hydrates them on demand. Cross-region round trips are the dominant cost in
this system (~635 ms/series measured in Phase 5), so N+1 at the API boundary is not a style
concern here — it is the difference between a responsive pane and an unusable one.

---

## 5. Invariants

Continuing Phase 5's numbering (I-1 … I-23 live in
[consolidation-architecture.md §5](consolidation-architecture.md)).

| | Invariant |
|---|---|
| **I-24** | A turn that assembled context has a persisted trace, or (G) failed and said so. Never a silent gap. |
| **I-25** | Turn and trace are atomic **with each other**; (G) never leaves an orphan of either. |
| **I-26** | (G) never writes `memories` and never runs on the direct-ingest path (I-11 preserved). |
| **I-27** | Citation validation is deterministic: no model calls, no prose parsing, no answer rewriting. |
| **I-28** | Every glass-box read endpoint is user-scoped, with cross-user denial asserted by test. |
| **I-29** | The API serves the persisted trace verbatim; it is never re-derived at read time. |
| **I-30** | An insight's `evidence_ids` are never added to `citable_ids` (§4.1), asserted by test. |

## 6. Documents this phase changes

| Document | Change | When |
|---|---|---|
| **ingestion-transaction-boundaries.md §12** | **Amendment.** Turn/trace persist at stage (G), post-narrate, not inside (D)/(D'). §4.3 carries the reasoning. | M1 |
| **09-decisions.md — ADR-13.14** | **Amendment.** Same: "one transaction after a turn completes" is *its own* transaction, not the memories'. | M1 |
| **09-decisions.md — ADR-14.8** | **Resolved.** Trace carries `citable_ids` (§4.2). | M1 |
| **09-decisions.md — ADR-16** | Q1 moves from *open* to *resolved* (§4.1). | M2 |
| **03-memory-engine.md §6** | Trace contract gains `citable_ids`. | M1 |
| **implementation-roadmap.md** | Phase 6 status as milestones land. | each |
| **TODOS.md** | Any deferral this phase creates. | as needed |

## 7. Risks

**[High] SSE through the shared ALB is unproven.** ADR-13.7 chose SSE for the live pane; Express
Mode's shared ALB may buffer streamed responses. *Mitigated by* proving it against the deployed
URL early in M6 and keeping the pane's data flow switchable to polling without a redesign.

**[High] Design-system scope creep.** ~10 working days remain against Phase 6 (5–7) plus Phase 7
(4–5). *Mitigated by* fixing tokens once in M4 and consuming them thereafter without
renegotiation.

**[Medium] The demo checkpoint requires the deployed URL**, which is blocked on the AWS work
recorded in TODOS.md. Phase 6 builds without it; the *demo* does not. Longest-lead blocker in
the project.

**[Medium] E2E lands last, at the deadline.** *Mitigated by* standing the Playwright harness up
with one trivial test in M4, so M8 writes tests rather than fighting CI browsers.

**[Low] Trace JSONB size.** A wide turn's trace carries evidence + ranking + steps. Payload-free
by construction (snapshots hold metadata, never payload copies), so growth is bounded by row
count, not content size.

## 8. Milestones

| | Milestone | Objective | Commit |
|---|---|---|---|
| M0 | Design lock | this document | ✅ `5aef6f5` |
| M1 | Trace persistence (T7a) | stage (G); `citable_ids` in the trace; ADR amendments | ✅ `32e2e1b` |
| M2 | Citation validation (T7b) | deterministic validator, honest scope | ✅ *this milestone* |
| M3 | Read API + hydration (T16) | trace/memory/timeline/stats endpoints, batch fetch | `feat(api): glass-box read API` |
| M4 | SPA foundation | toolchain, design system, shell, state primitives | *awaiting the design system* |
| M5 | Chat + chips + receipts | build-order 1–3 | |
| M6 | Live engine pane | evidence rows, query display, SSE | |
| M7 | Timeline + stats | build-order 7 | |
| M8 | Responsive/a11y/perf + E2E + ADR-17 | phase close | |

**M4–M8 are deliberately unelaborated here.** The frontend design system, component library,
animation guidelines, and frontend engineering rules are being authored separately and will
govern every React component; specifying UI structure now would pre-empt them.

## 9. Test strategy

Beyond each milestone's own block, three tests carry the phase:

1. **The property test — no context without a trace.** For any turn that assembled context, a
   persisted trace exists (I-24). This is the phase's equivalent of Phase 5's serde guard.
2. **The false-positive test for ADR-14.8.** An answer citing an *aggregated* memory validates
   `valid`. This is the defect §4.2 exists to close; without it the fix is unproven.
3. **Cross-user denial, per endpoint** (I-28) — asserted at the route, not the query.

Per ADR-15.6, each milestone asserts at least one thing at the **committed-row layer**, not only
at the unit that owns the logic.

## 10. Open questions

| | Question | Owner |
|---|---|---|
| **Q4** | Does the UI read traces by `turn_id` or `thread_id`? *Recommended:* `turn_id`, with thread scoping at the list endpoint. Affects the SSE contract. | M3 |
| **Q5** | Does the live pane push full traces or deltas? *Recommended:* full — payloads are small and there is no reconciliation state to get wrong. | M6 |

## 11. Maintenance notes

- **Do not widen `citable_ids` to include insight `evidence_ids`** without re-reading §4.1. The
  test asserting the narrow surface is the guard; deleting it is the decision.
- **Do not move (G) inside the ingestion transaction** to make the trace atomic with the
  memories. §4.3 is the reasoning, and the 21½-minute `DELETE` is the precedent.
- **Do not re-derive a trace at read time** (§4.5, I-29). A rendered glass box that can drift
  from what the turn did is worse than no glass box.
- **Do not let the UI imply "verified"** where the validator proves "resolvable" (§4.4).

## 12. Related files

| File | Relationship |
|---|---|
| [engine/trace.py](../../engine/trace.py) | The trace contract; gains `citable_ids` in M1 |
| [engine/assembly.py](../../engine/assembly.py) | Emits context + trace as a pair; `citable_ids()` |
| [agent/graph.py](../../agent/graph.py) | Where stage (G) attaches, after `narrate` |
| [engine/schema.sql](../../engine/schema.sql) | `turns`, `evidence_traces` — created in Phase 2, written here |
| [ingestion-transaction-boundaries.md](ingestion-transaction-boundaries.md) | §12, amended by §4.3 |
| [consolidation-architecture.md](consolidation-architecture.md) | Phase 5's decisions and I-1…I-23 |
| [07-glass-box-ui.md](../office-hours/07-glass-box-ui.md) | Philosophy, grammar, build order |
