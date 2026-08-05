# Consolidation & Insight Engine Architecture (Phase 5 / T5 · T6 · T12)

> Engineering deep dive (see [README.md](README.md) conventions). **Status: LOCKED 2026-08-03
> (approved as written).** This is the architecture for Phase 5, the same way
> [replay-architecture.md](replay-architecture.md) was locked for Phase 4: read it before
> writing any Phase 5 code, and do not re-litigate §4 silently. Changes are permitted only
> when a **genuine implementation issue** is discovered — in which case stop, raise it, and
> amend this document explicitly, exactly as Phase 4 did for its §4.1 and §4.14 amendments.
>
> Decisions it implements or amends: [ADR-3](../office-hours/09-decisions.md#adr-3) (event-driven
> consolidation), [ADR-9](../office-hours/09-decisions.md) (retraction never deletes),
> [ADR-13.1 / 13.10 / 13.11 / 13.12](../office-hours/09-decisions.md#adr-13),
> [ADR-14.7 / 14.9 / 14.11 / 14.12](../office-hours/09-decisions.md#adr-14),
> [ADR-15.1 / 15.4](../office-hours/09-decisions.md#adr-15).
> Design context: [03-memory-engine.md §4](../office-hours/03-memory-engine.md),
> [04-database-design.md](../office-hours/04-database-design.md),
> [06-retrieval-strategy.md](../office-hours/06-retrieval-strategy.md).
> Backlog: [T5 / T6 / T12](../office-hours/11-implementation-tasks.md).

---

## 1. Why this document exists

Phase 5 is the phase where **the memory stops recording and starts claiming**. Every earlier
phase wrote or returned facts the user themselves supplied; consolidation writes rows that
assert something the user never said. Three properties therefore have to be pinned down before
any code exists, because getting them wrong produces work that *looks* green and is wrong —
the failure class [ADR-15.6](../office-hours/09-decisions.md#adr-15) named at the end of Phase 4:

1. **What may honestly be claimed** from the data that actually exists (§4.1–§4.3).
2. **How a claim is identified over time**, so re-deriving it does not duplicate it (§4.6).
3. **Where the claim is computed**, so it cannot violate the transaction and determinism
   boundaries the write and read paths already depend on (§4.8–§4.9).

The Phase 5 architecture review found these as blocking items R1/G1, R2, R6/R7. §4 resolves them
with recommendations, rejected alternatives, and consequences. §5 lists the invariants this
introduces, §6 the documents that must change, §8 the milestone plan.

---

## 2. Scope

**In scope**

| | Deliverable | Task |
|---|---|---|
| a | Two insight detectors (`level_shift`, `intervention_outcome`), deterministic | T6 |
| b | Insight rows: hypothesis, lineage, `pattern_strength` with published components, typed `retraction_condition` | T5, T6 |
| c | Synchronous, budgeted consolidation riding ingestion + on-demand `analyze_series` | T6 |
| d | Typed retraction evaluation; retract/supersede, never delete | T5 |
| e | Insight-lookup retrieval family + insight lineage in `EvidenceTrace` | (06's sixth family) |
| f | `cli/consolidate.py` — one-shot retroactive pass over already-replayed history | **new, see §4.11** |
| g | Latency profile → `docs/latency.md` | T12 |
| h | Photo ingestion: S3 → Bedrock vision → meal events | (M2 item) |

**Out of scope, deliberately (§4.18)**

| | Item | Why |
|---|---|---|
| i | Entity canonicalization | its cheap window closed when replay committed; see §4.18 |
| j | Period-aware aggregation (one row per period) | needs its own ADR and touches builders behind 498 tests; §4.2 buys most of the benefit without touching them |
| k | `ruptures` PELT + the 7–35 day lag scan | no data can exercise either; §4.3 |
| l | Retracted-insight browsing UI | Phase 6 |

---

## 3. The evidence base (measured, not assumed)

Every decision in §4 is grounded in what the account actually contains after the Phase 4 run.
Counts below were taken from `data/replay/history.jsonl` — the exact 424 records ADR-15 committed.

### 3.1 Type distribution

| Type | Rows | Span | Consolidatable metric |
|---|---|---|---|
| supplement | 288 | 2026-03-18 → 06-30 | dose present on 88/288; **names conflate across products** |
| meal | 97 | 2026-03-26 → 06-30 | **`protein_g` — 97 distinct days** |
| note | 22 | 2006-08-14 → 2026-07-06 | none (prose — engine must never parse it) |
| workout | 14 | 2026-06-16 → 06-30 | `duration_min`/`distance_km` are **deliberately `null`** |
| blood_report | **2** | 03-25, 07-03 | markers, **absent from `METRICS`** |
| body_scan | **1** | 06-20 | `body_fat_pct` |
| weight | **0** | — | — |
| sleep | **0** | — | — |

**399 of 424 rows carry `expanded_from`** — they are materialized period days
([ADR-15.4](../office-hours/09-decisions.md#adr-15)), not independent observations.

### 3.2 The protein series, in full

The only daily metric series in the account. Printed exhaustively because its shape decides §4.2
and §4.3:

| Date | g/day | Source assertion |
|---|---|---|
| 2026-03-26 | 31.0 | `meal-pattern.2026-03-26.2026-04-24` |
| 2026-04-25 | 36.0 | `meal-pattern.2026-04-25.2026-06-14` |
| 2026-06-15 | 45.0 | `meal-pattern.2026-06-15.2026-07-05` |
| 2026-06-23 | 83.0 | *same assertion*, `segments` split |

**Four distinct values across 97 days, produced by three reviewed payload-table entries.** Zero
within-segment variance. This is a step function written by the converter, not a signal measured
from a body.

### 3.3 The real structure of this account

Reading the notes and supplement assertions together, the account is **not** a set of noisy time
series. It is a chain of **dated interventions bracketed by two measurements**:

```
2026-03-25  ▸ blood report E01     Vitamin D 6.2 · B12 152 · ferritin 96.7
2026-03-26  ▸ diet             first non-veg; 4 eggs + 200 g dahi/day   protein 0 → 31
2026-03-28  ▸ supplement       Vitamin D 60,000 IU weekly + B12 daily
2026-04-25  ▸ diet             eggs → 6/day                             protein 31 → 36
2026-06-15  ▸ diet             3 eggs + 100 g paneer + 250 g dahi       protein 36 → 45
2026-06-16  ▸ workout          first gym session ever (series onset)
2026-06-23  ▸ diet             + 100–150 g chicken/day                  protein 45 → 83
2026-06-24  ▸ supplement       Vitamin D reduced to fortnightly  ← the clinician's own signal
2026-07-03  ▸ blood report E02     Vitamin D 38.4 · B12 752
```

That shape — *sparse outcome measurements, dense behavioural changes between them* — is not an
accident of this dataset. It is how health data is generated in general: you eat every day and
get a blood panel twice a year. **The architecture below is built for that shape**, which is why
§4.1 is a design decision rather than a demo workaround.

---

## 4. Decisions

### 4.1 Insight kinds: `level_shift` and `intervention_outcome` — not changepoint detection *(resolves R1 + G1's "what")*

**Decision.** The engine emits exactly two kinds of derived insight, both deterministic:

| Kind | Claim shape | Fires when |
|---|---|---|
| **`level_shift`** | *"metric M moved from A to B beginning on date D, and stayed there"* | a whitelisted metric's level changes between adjacent observation segments |
| **`intervention_outcome`** | *"measured marker M went A → B between dates D₁ and D₂; these N structurally-detected changes fall inside that interval"* | a sparsely-measured metric gains a second (or later) measurement, with ≥1 intervention in the interval |

An **intervention** is defined structurally, never from prose (§4.4). Neither kind is presented
as causal; `intervention_outcome` in particular is explicitly a *co-occurrence with a hypothesis*,
and its specificity factor (§4.13) drops as the number of competing changes rises — the engine
scores its own attribution down when several things changed at once.

**Why this and not statistical changepoint detection.** Because the data — and the domain —
cannot support the latter honestly:

- The only daily series in the account (§3.2) is a four-level synthetic step function. A
  changepoint detector run on it would rediscover the converter's own segment boundaries with
  infinite effect size, then publish a near-perfect `pattern_strength` for an artifact. The glass
  box invites judges to click into that evidence, where they would find ~30 identical rows.
- The **outcome** series the product actually cares about — blood markers, body scans — have 2
  and 1 points respectively. No amount of algorithmic sophistication extracts a changepoint from
  two points; but two points *are* exactly enough for an honest before/after statement, which is
  what a clinician would say.
- The design's flagship printed example — *"protein ↑ + sleep ≥7.5 h preceded body-fat decline
  (lag ≈ 3 wk)"* ([03](../office-hours/03-memory-engine.md), [04](../office-hours/04-database-design.md))
  — requires sleep (0 rows) and body-fat (1 row). It is unbuildable, and no reordering of the
  milestones makes it buildable.

**Why this serves the demo without bending toward it.** Both money moments fall out of the general
design rather than being special-cased:

- *"What changed before my Vitamin D recovered?"* is an `intervention_outcome` over
  `vitamin_d_ng_ml` (6.2 → 38.4, 2026-03-25 → 07-03), with the Vitamin D supplement onset three
  days after the low reading among its cited interventions. This is the **"it had already flagged
  it"** clause of the project's own one-paragraph pitch, now literally true.
- The **live beat** — log a body scan on camera and watch an insight appear — is an
  `intervention_outcome` over `body_fat_pct`: the historical scan (2026-06-20, 39.2 %) plus the
  one just logged. Exactly one intervention falls inside that interval (the 2026-06-23 protein
  level shift), so specificity is 1.0 and the insight scores *high* with a single clean
  attribution. `created_at = now`, truthfully, which is the one place
  [ADR-13.10](../office-hours/09-decisions.md#adr-13) permits "flagged the moment it happened".

**Rejected alternatives**

| Alternative | Why rejected |
|---|---|
| **PELT over the daily series as written (ADR-13.12 as-is)** | Detects the converter, not the user. Publishes a high strength score for a synthetic artifact — an ADR-4 violation in the most judge-visible surface the product has. |
| **PELT restricted to genuinely observed (non-`expanded_from`) series** | Honest, but zero series qualify today and none will during the hackathon (live logging yields a handful of points). Shipping numpy + scipy + ruptures (~150 MB) into a 0.25 vCPU / 0.5 GB Fargate task for a path that cannot fire is precisely the *"no infrastructure built solely for completeness"* rule the builder set. Same reasoning that deleted the replay extraction cache ([ADR-15.2](../office-hours/09-decisions.md#adr-15)); re-add trigger recorded in §10. |
| **Emit insights anyway, with a disclaimer** | A disclaimer next to a confident number is how the analytics-pseudo-rigor finding (outside voice #9) came back in the first place. ADR-13.12 exists to prevent exactly this. |
| **Reconstruct more history to manufacture a series** | Inventing data to feed an algorithm inverts ADR-4. |
| **Drop derived insights from Phase 5** | Two-tier memory *is* the differentiator (ADR-1/ADR-2). The tier-2 hole is already cut in five places in the code. |

**Long-term consequences.** The engine's insight vocabulary is anchored to *how health data is
actually generated* (sparse outcomes, dense behaviours) rather than to a time-series library.
Adding a third kind later is additive: the detector interface (§4.3) takes a series and returns
`Finding`s, so a future `lag_correlation` slots in beside these two without touching persistence,
identity, retraction, or retrieval. The cost is that the phrase "changepoint detection" leaves the
product vocabulary — the timeline strip's `◆ May 12 protein ↑` marker now renders a `level_shift`,
which is the same UI affordance under a more honest name.

**ADRs to update:** ADR-13.12 (amendment — detector set), ADR-16 (new, records this).
**New invariants:** I-1, I-2, I-3 (§5).

---

### 4.2 Consolidation reads series at the **assertion** level, never the materialized day *(resolves R2)*

**Decision.** When building a series, contiguous rows sharing an `expanded_from.composition` **and
the same metric value** collapse into **one observation** carrying `(value, start, end,
n_materialized, assertion_text)`. Rows without `expanded_from` are one observation each. Detectors
see observations, never raw days.

Evidence lineage is **boundary-anchored**: an insight cites the first and last memory of each
contributing segment plus every contributing point event, capped at `MAX_EVIDENCE_IDS = 24`, and
carries `evidence_count` stating the true total. The parent `assertion` text rides in the insight
payload so the glass box shows *"4 eggs + 200 g dahi daily, 2026-03-26 → 04-24"* rather than a wall
of identical days.

**Why.** [ADR-15.4](../office-hours/09-decisions.md#adr-15) states the honesty mechanism for
expanded rows as **two signals** — lowered confidence *and* the `expanded_from` marker — and warns
that shipping only the first is an ADR-4 violation. A consolidation pass that treats 30
materialized days as 30 independent observations reads the second signal and discards it,
re-committing exactly the error that had to be repaired by metadata backfill during the first
production run. Collapsing is also the *correct* statistics: the user asserted one fact, so the
engine has one observation.

This additionally delivers most of what [replay-architecture §8 handoff 2](replay-architecture.md)
called "the known-better architecture" (period-aware aggregation) **without touching a single
Phase 3 query builder** — the collapse happens in the consolidation reader, above the builders,
behind 498 untouched tests.

**Rejected alternatives**

| Alternative | Why rejected |
|---|---|
| **Read raw daily rows** | §4.1's artifact problem; also inflates every coverage/count term by ~30×. |
| **Exclude `expanded_from` rows entirely** | Removes 94 % of the account. Protein — the only behavioural series — disappears, and with it every intervention the money question depends on. |
| **Implement full period-aware aggregation now** (one row per period, materialized during aggregation) | The right long-term design, and still deferred: it modifies `aggregate_memories`, needs a double-count rule for "live-logged 4 eggs on a day inside a '4 eggs daily' period", and needs its own ADR. Deferred on scope, not merit — unchanged from replay-architecture §8. |
| **Cite all contributing IDs uncapped** | A 105-row supplement segment yields a 105-ID lineage; the lineage graph (Phase 6, already first-to-cut) becomes unrenderable. |

**Long-term consequences.** A single collapse function becomes the boundary between "how memories
are stored" and "what the analytics believes it observed". When period-aware aggregation eventually
lands, this function becomes a no-op rather than a conflict — periods will already arrive as one
row. The cap introduces a place where lineage is *sampled*; `evidence_count` is what keeps that
honest, and the Phase 6 UI must render it (§6).

**ADRs to update:** ADR-15.4 (note: consolidation's assertion-level read is the partial answer to
handoff 2), ADR-16.
**New invariants:** I-4, I-5.

---

### 4.3 Detector set and the `ruptures` amendment

**Decision.** Detectors live in `engine/analytics.py` as **pure functions with no I/O, no clock,
and no model**: `list[Observation] → list[Finding]`. Two are implemented. `ruptures` is **not** a
dependency of this project.

```
detect_level_shifts(obs, *, min_effect, min_span_days)       -> list[Finding]
detect_intervention_outcomes(measurements, interventions, *, …) -> list[Finding]
```

Daily bucketing keeps its ADR-13.12 rule unchanged and non-negotiable: **gaps stay missing; health
data is never interpolated.**

**Why the amendment.** ADR-13.12 named `ruptures` PELT and a bounded 7–35 day lag scan over
whitelisted series pairs. Measured against the data (§3): PELT has nothing honest to run on
(§4.1), and the lag scan has **no valid pair** — it needs two overlapping continuous series, and
the account has one (protein), since workout metrics are null by design, supplement doses conflate
products, and sleep/weight are empty. The 7–35 day bound itself was derived from the
protein→body-fat hypothesis that §4.1 shows is unbuildable. Keeping either would ship unexercised
infrastructure; the repo already has a precedent for deleting rather than keeping it
([ADR-15.2](../office-hours/09-decisions.md#adr-15)), including the discipline of recording the
re-add trigger, which §10 does.

**Rejected alternatives:** vendoring a minimal PELT (same dead path, now hand-maintained); keeping
`ruptures` as an optional extra (an import path exercised by no test is worse than no import path).

**Long-term consequences.** The production image stays small and numpy-free; app startup keeps its
current cost. If a user ever accumulates a genuinely observed dense series, §10's trigger and
recipe restore PELT behind the same `Finding` interface, with the detector registry as the only
edit.

**ADRs to update:** **ADR-13.12 amendment** (detector set; pattern-strength factor names, §4.13).
**New invariants:** I-6 (no interpolation — restated, unchanged), I-7 (detectors are pure).

---

### 4.4 What a "series" is, and what an "intervention" is *(resolves C5, C8)*

**Decision — series.** A `SeriesKey` is `(kind, metric)` where `kind ∈ {behavioural, outcome}` and
`metric` names an entry in a new closed vocabulary `CONSOLIDATION_SERIES`, a **subset** of
`engine/retrieval.METRICS`:

Each entry also declares an **`EffectScale`** — `min_delta` (noise floor) and `full_delta`
(full-size change), both in the metric's own units (§4.13 amendment). The values below are
product heuristics with their basis recorded beside them in `engine/insights.py`.

| Metric | Kind | Detector | `min_delta` | `full_delta` | Notes |
|---|---|---|---|---|---|
| `protein_g` | behavioural | `level_shift` | 5 g/day | 30 g/day | the account's one dense series |
| `sleep_hours` | behavioural | `level_shift` | 0.5 h | 1.5 h | dormant today, correct when live-logged |
| `body_fat_pct` | outcome | `intervention_outcome` | 1.5 pts | 8 pts | scans are sparse; BIA varies ±1–2 pts |
| `weight_kg` | outcome | `intervention_outcome` | 1.5 kg | 8 kg | |
| `vitamin_d_ng_ml` | outcome | `intervention_outcome` | 5 | 20 | §4.5 |
| `vitamin_b12_pg_ml` | outcome | `intervention_outcome` | 50 | 300 | §4.5 |
| `ferritin_ng_ml` | outcome | `intervention_outcome` | 15 | 60 | §4.5 |
| `ldl_mg_dl` | outcome | `intervention_outcome` | 10 | 40 | §4.5 |
| `hba1c_pct` | outcome | `intervention_outcome` | 0.3 | 1.0 | §4.5 |

A meal therefore touches **one** series (`protein_g`), not four — carbs/fat/kcal stay aggregatable
but are not consolidatable. This is the primary defence of the time budget (§4.8).

**Decision — intervention.** An intervention is one of exactly two **structurally detectable**
events, never a reading of prose:

1. **`series_onset`** — the first memory of a type ever (e.g. first workout), or the first memory
   whose `payload.name` exactly equals a value not seen before for `type='supplement'`.
2. **`level_shift`** — a `Finding` already produced by the level-shift detector on a behavioural
   series.

**Why.** The engine never interprets language ([06](../office-hours/06-retrieval-strategy.md#query-planning),
ADR-14). The notes in §3.3 read like a perfect intervention log — *"started Vitamin D 60,000 IU
weekly"* — and mining them would be the single most tempting boundary violation in this phase. It
is forbidden: the moment consolidation parses a note, the deterministic layer contains an NL
interpreter and every glass-box claim about determinism becomes false. Both definitions above
reduce to exact equality and arithmetic. Notes remain fully retrievable by the normal semantic
path, so the *narrator* can still surface them; the *engine* never reads them.

**Rejected alternatives:** an LLM-extracted "intervention" event type at ingestion (adds a new
extraction contract, a new failure mode, and nondeterminism to the tier-2 write path); keyword
matching over note text (a synonym table that rots — already rejected for entity canonicalization
in TODOS).

**Long-term consequences.** Supplement intervention detection depends on exact `payload.name`
equality, which is sound for the replayed history (names are converter-consistent) and adequate for
live logging, but is the one place where §4.18's deferral of entity canonicalization is felt: two
spellings of the same supplement would read as two onsets. Recorded as an accepted, bounded
limitation, mitigated by the `MIN_INTERVENTION_GAP_DAYS` de-duplication in §4.13's specificity term.

**New invariants:** I-8 (interventions are structural), I-9 (consolidatable metrics are a closed
subset).

---

### 4.5 Blood markers enter `METRICS` as a curated, closed set *(resolves R9)*

**Decision.** Add five marker metrics to `engine/retrieval.METRICS` with path
`("markers", "<key>")` on `type='blood_report'`: `vitamin_d_ng_ml`, `vitamin_b12_pg_ml`,
`ferritin_ng_ml`, `ldl_mg_dl`, `hba1c_pct`. Curated by hand, reviewed, closed — exactly like every
existing entry.

**Why.** [06](../office-hours/06-retrieval-strategy.md#query-construction) requires the planner's
metric vocabulary to be a whitelist so *"the tool schemas the planner sees cannot name a metric the
engine does not have"*. `BloodReportPayload.markers` is `dict[str, float]` with open keys, so it has
no typed hot field to derive from. A hand-curated subset keeps the vocabulary closed and, as a free
side effect, makes the money question's own numbers reachable through `aggregate_memories` and
`lookup_events` — today they are not addressable by any tool at all.

`MetricDef` already supports arbitrary path tuples, so this is a data change, not a code change.

**Rejected alternatives:** deriving the enum from observed marker keys per user (breaks the closed
vocabulary and makes the tool schema user-dependent); a generic `blood_marker(name=…)` slot (a
free-text slot below the tool-call boundary — ADR-14.11 strict-slots violation).

**Long-term consequences.** The metric enum grows by five and will grow further as panels expand;
each addition stays a deliberate reviewed line. Marker keys carry units in the name (`_ng_ml`),
matching the existing `qty_g` / `duration_min` convention the extraction prompt already teaches.

**Docs to update:** 06 (builder-families table), 04 (payload conventions example).

---

### 4.6 Insight identity: `(user_id, kind, series_key)`, updated by supersession *(resolves R6)*

**Decision.** An active insight is unique per `(user_id, kind, series_key)`. Consolidation
recomputes, then compares a **finding fingerprint** — a deterministic string over the claim's
defining content (kind, series key, boundary dates, values rounded to a fixed precision, intervention
id set) — against the active insight's stored `fingerprint`:

| Outcome | Action |
|---|---|
| no active insight | insert |
| fingerprint identical | **write nothing** |
| fingerprint differs | insert replacement **and** `mark_superseded` the prior row, in one transaction |
| detector now refuses to emit (§4.15) | leave the existing insight active; do not delete or retract — the evidence has not contradicted it |

The insert+supersede transaction reuses the shape `reprocess_note` and `ingest_events_superseding`
already share; **no third transaction shape is introduced**, per replay-architecture §4.14's
explicit stop-and-ask rule.

**Why.** The write path has no deduplication by design ([ADR-15.1/15.3](../office-hours/09-decisions.md#adr-15))
— correct for live chat, catastrophic for a pass that runs on **every ingest touching a series**.
Without an identity rule, ten meals write ten copies of the same claim; the top-bar `23 insights`
stat becomes `4,182 insights`, the lineage graph becomes noise, and the tier-2 boost (§4.16) fills
the narration budget with duplicates. This is the Phase-5 analogue of Phase 4's P0 duplicate risk,
and it deserves the same treatment: an explicit identity key, enforced by a property test.

Fingerprinting rather than deep-comparing payloads keeps the comparison cheap, deterministic, and
independent of incidental payload changes (a reworded hypothesis string must not trigger a
supersession).

**Amended 2026-08-04 (M5a) — identity keys on the claim's dates, not its evidence window.**
The first implementation fingerprinted `window_start`/`window_end`. For a `level_shift` those bound
the *compared observations*, and the post-side observation grows every time another day at the same
level is logged — so three identical lunches on three consecutive days produced **three** insight
rows with identical kind, series, boundary and values. No *active* duplicates, so this milestone's
own property test still passed, but a user logging lunch daily would accumulate ~30 superseded rows
a month for one unchanged claim, filling the lineage chain — where "the engine changed its mind" is
supposed to be legible — with noise.

`Finding.claim_dates` now supplies what identifies a claim: for `level_shift` the **boundary alone**
("moved from A to B starting here"), for `intervention_outcome` the **measurement pair** (there the
window *is* the claim, and adding behavioural rows between two measurements moves neither).
`window_start`/`window_end` remain on the payload unchanged — they are what the claim is *about*,
and §4.12 needs `window_end` for `event_time`. Every real change is still caught: a different
boundary changes the dates, a different level changes the values, different interventions change the
id set. **Accepted consequence:** when evidence accumulates at an unchanged level, `coverage` (and
so `pattern_strength`) can drift slightly stale on the stored row until the claim itself changes —
correct under this section, since a score is not the claim, and §4.7's derived freshness already
tells `analyze_series` when a recompute is warranted.

**Found at the M3→M5 seam**, exactly the failure class [ADR-15.6](../office-hours/09-decisions.md#adr-15)
named: M3 tested `consolidate_series` directly, where nothing extends the series between runs, so
both sides were individually correct and the defect lived between them.

**Rejected alternatives**

| Alternative | Why rejected |
|---|---|
| **No identity — append every derivation** | The duplicate explosion above. |
| **UPDATE the existing insight in place** | Violates ADR-9 (the engine's history of being wrong is itself memory) and destroys the supersession-chain demo material. |
| **Delete-then-insert** | ADR-9 forbids deletion outright. |
| **Content-hash the whole payload as the key** | Makes an insight's identity change whenever its prose changes — the exact failure replay-architecture §4.3 closed for `record_id`, re-created one layer up. |

**Long-term consequences.** Insight rows accumulate as chains rather than as duplicates, which is
the intended demo material (*"the engine changed its mind, here is the chain"*). A user with a long
history holds O(kinds × series) active insights — a bounded, small number — plus a superseded tail
that grows only when a claim genuinely changes.

**New invariants:** I-10 (one active insight per identity), I-11 (no third transaction shape),
I-12 (recompute over unchanged data writes zero rows).

---

### 4.7 Freshness is **derived**, not stored *(resolves C4)*

**Decision.** No `last_evaluated_at` field is stored. An active insight is **stale** iff its series
contains a memory with `created_at > insight.created_at`. `analyze_series` and the insight-reuse
path evaluate that predicate with one indexed query.

**Why.** [06](../office-hours/06-retrieval-strategy.md#insight-reuse) specifies
`last_evaluated_at` "maintained by the consolidation pass", which implies mutating a payload field
on every no-op recompute. Today `memories` is append-only except for `status`, `superseded_by`, and
`embedding` — a genuinely valuable invariant that makes the table's audit story trivial. Deriving
freshness preserves it exactly, needs no column, no migration, and no new mutation class, and it is
*more* correct bi-temporally: `created_at` already means "when we learned it"
([04](../office-hours/04-database-design.md)), so "has anything been learned about this series since
this claim was derived?" is the literal reading of the schema.

**Rejected alternatives:** a payload field (introduces payload mutation for a value that is
derivable); a dedicated column (a migration and a second source of truth for the same fact, which
can then disagree).

**Long-term consequences.** The freshness check costs one `MAX(created_at)` query per series instead
of zero, paid only on `analyze_series`, not on the ingest path. `memories` keeps its narrow
mutation surface, which is what makes I-13 auditable.

**Docs to update:** **06 — the insight-reuse section must drop `last_evaluated_at`.**
**New invariants:** I-13 (`memories` mutations remain limited to `status`, `superseded_by`,
`embedding`).

---

### 4.8 Consolidation runs **post-commit, best-effort, budgeted** — stage (F₀) *(resolves R3, C11)*

**Decision.** Consolidation is inserted into the ingestion pipeline between the receipt and the
opportunistic backfill, and its outputs are appended to the receipt before it returns:

```
├─ (D) ═══ SINGLE WRITE TRANSACTION ═══  insert the turn's typed memories  COMMIT
├─ (E) build receipt from committed rows
├─ (F₀) CONSOLIDATION  — budgeted, own transaction(s), best-effort   ← new
│         · resolve touched series from the committed rows
│         · per series: read → collapse (§4.2) → detect → identity-compare (§4.6)
│         · insert/supersede insights; evaluate retraction conditions (§4.14)
│         · created insights are appended to the receipt
├─ (F₁) opportunistic embedding backfill  (unchanged)
└─ return receipt
```

Four rules:

1. **Never inside the turn's transaction.** Rule 1 of
   [ingestion-transaction-boundaries.md](ingestion-transaction-boundaries.md) forbids network I/O
   in a transaction, and rule 2's atomic-turn guarantee must not be widened to cover derived data.
2. **A consolidation failure never fails a turn** — same posture as backfill (`logger.exception`,
   swallowed). An insight lost to an error costs nothing: the next ingest re-derives it, and §4.6
   makes that idempotent.
3. **Insights are written with `embedding = NULL`** and embedded by the existing (F₁) backfill.
   This removes a Bedrock round trip from the budget entirely and reuses a tested mechanism (T15)
   rather than adding a model call to the hot path.
4. **The budget is a deadline, checked between steps** (`time.monotonic()`, injectable clock), not
   a thread or a timeout. Exceeding it stops cleanly after the current series and records the
   deferral; ingestion has already committed and is unaffected.

**The 300 ms number is provisional and T12 must re-derive it.** [deploy.md](../deploy.md) records
the topology: app in **us-east-1**, CockroachDB Cloud in **ap-south-1**. A single round trip on that
path is ~200–250 ms, and (F₀) needs 2–3. ADR-13.1's ~300 ms predates that topology. T12 (M6)
measures the real number; if the budget must change, that is an **ADR-13.1 amendment recorded
honestly**, not a silently relaxed constant. §4.4's one-series-per-meal rule and rule 3 above are
the two structural defences that give the budget its best chance.

**Why post-commit rather than pre-commit or inside.** Inside violates rule 1. Pre-commit would make
a derived-data failure able to lose the user's actual input — inverting never-lose-input for the
sake of a hypothesis. Post-commit is also what makes the live demo beat work at all: the receipt is
built from committed rows, so the insight it announces is one that definitely exists.

**Rejected alternatives:** an async queue/worker (ADR-13.1 already rejected it — infra for a
single-user-scale demo); consolidating on read only (kills the live beat, which is the phase's
whole demo checkpoint).

**ADRs to update:** ADR-13.1 (amendment: stage placement now specified; budget value pending T12),
ingestion-transaction-boundaries.md §4 (the (F₀) stage) and §12.
**New invariants:** I-14 (consolidation never inside the turn transaction), I-15 (consolidation
failure never fails a turn), I-16 (insights are written unembedded).

---

### 4.9 `analyze_series` is **graph-dispatched like `log_memory`**, not a query builder *(resolves R7)*

**Decision.** `analyze_series` is offered in the planner's tool vocabulary (so routing stays tool
selection, ADR-14.1) but is **not** routed through `prepare_call`/`execute`. The graph dispatches it
to `ConsolidationService`, exactly as it already dispatches `log_memory` to `IngestionService`:

```
plan → [analyze_series selected] → consolidate node → retrieve → assemble → narrate
```

Ordering follows ADR-14.3's logic: consolidation runs **before** retrieval, so an insight derived
this turn is visible to the same turn's insight lookup. Slots: `{metric: enum(CONSOLIDATION_SERIES)}`
only — no date range (the series defines its own extent), no timezone (ADR-14.10, engine-injected).

**Why.** [agent/graph.py](agent/graph.py) executes every retrieval tool inside **one shared
`db.transaction()`**. `analyze_series` writes, and writing requires a read, a compare, and possibly
an insert+supersede — a transaction inside a transaction, holding the outer one open across all of
it. Every other builder is a pure read returning `(result, RetrievalStep)`; forcing a writer into
that contract would make `RetrievalOutcome` a union of "things that read" and "things that mutate",
and would put the §4.8 budget inside a transaction it must never enter. The `log_memory` precedent
already exists precisely because ingestion "is not a query builder and needs a service rather than a
cursor" — `analyze_series` is the same animal.

**Rejected alternatives**

| Alternative | Why rejected |
|---|---|
| **A read-only `analyze_series` that returns findings without persisting** | Then consolidation is not event-driven for query turns, insights are recomputed on every ask, and 06's insight-reuse contract has nothing to reuse. |
| **Persist as a post-`retrieve` side effect** | Hides a write behind a name and an interface that says "read". The next maintainer adds a second writer to the same transaction. |
| **Widen the retrieve transaction to permit writes** | Breaks transaction-boundaries rule 1 the moment an embedding is needed, and lengthens lock hold times on a cross-region link. |

**Long-term consequences.** The tool vocabulary gains a second "verb" tool alongside `log_memory`,
which makes the read/write split in the agent layer explicit rather than incidental. `agent/tools.py`
grows an `is_analyze_series` predicate mirroring `is_log_memory`; the closed builder set stays
read-only, which keeps `execute()`'s contract — *"no model calls, no language, no raw SQL"* — plus a
new implicit *"no writes"*.

**Note:** `agent/tests/test_tools.py:185` currently asserts `analyze_series` is rejected as unknown.
That test flips meaning in M5 and must be updated deliberately, not deleted.

**ADRs to update:** ADR-16; 03's tool table (dispatch note); 05's mermaid (consolidate node).
**New invariants:** I-17 (the retrieval builder set stays read-only).

---

### 4.10 Insight lookup is a sixth builder family; lineage reaches the trace through it *(resolves R14)*

**Decision.** Add the `insight` builder family that
[06](../office-hours/06-retrieval-strategy.md#query-construction) already reserves. It returns a
typed `InsightResult` whose entries carry `id, hypothesis, evidence_ids, pattern_strength,
window_start, window_end, status` — **not** an `EvidenceSnapshot`. `assemble()` maps those to
`InsightRef` and populates `EvidenceTrace.insights`.

**Why.** `EvidenceSnapshot` is documented as *"deliberately payload-free"*
([engine/trace.py](engine/trace.py)) — the ADR-12 rule that a trace references memories and never
copies payloads. Insight lineage (`evidence_ids`) lives *in* the payload. Retrieving insights
through the existing snapshot-shaped families would therefore either lose the lineage or force a
payload copy into the snapshot — and this is not hypothetical: the 2026-07-29 manual validation hit
the identical conflict for meal quantities and correctly refused to breach the boundary
([12-test-plan.md, defect 2](../office-hours/12-test-plan.md#manual-end-to-end-validation-record--2026-07-29)).

A dedicated family resolves it cleanly: `InsightRef` is a *distinct* trace type that already exists
and is already specified to carry lineage, so nothing about ADR-12 bends. Assembly stays a **pure
function** (ADR-14.7) — it maps what the retrieval outcome brought, and performs no I/O.

**Rejected alternatives:** adding `payload` to `EvidenceSnapshot` (breaches ADR-12, and the
precedent above says surface the conflict instead); hydrating lineage inside `assemble()` (breaks
ADR-14.7 purity, the mechanism behind ADR-14.8).

**Implemented at M5b.** Four points settled while building it:

- **The family is read-only (I-17), and freshness is deliberately not its job.** Deciding a claim
  is stale and recomputing it is a *write*; a question must not write. That belongs to
  `analyze_series` (M5c), which the graph dispatches outside the retrieve transaction precisely
  because it does.
- **Insights bypass the raw-event budget**, like aggregates and counts. An insight that already
  answers the question is the last thing that should be crowded out by the events it summarises —
  06's tier axis applied at the budget, not only at the score.
- **The richer representation wins on a tie.** The same insight can arrive as a payload-free
  snapshot through recall *and* as a full row through this family. 06's "one memory is one
  candidate" rule resolves it in favour of the row that carries lineage; the snapshot is dropped
  from the ranked candidates and from `trace.evidence`.
- **No date-range slot.** An insight already carries the window it is about, so a planner-supplied
  range would filter claims by *when they were derived* rather than what they are about — a subtly
  wrong answer that looks right.

**Q1 is preserved, not pre-empted.** `citable_ids()` gains each participating insight's **own id**
and deliberately **not** its `evidence_ids`. Whether the narrator may cite the rows underneath a
claim is T7's call alongside ADR-14.8, and a citable surface is far easier to widen later than to
narrow. A test asserts the lineage is absent from the surface, so widening it has to be a
deliberate act.

**Long-term consequences.** The trace's `insights` array becomes real, which unblocks Phase 6's
lineage graph. The citable surface (`ContextBlock.citable_ids`) must gain insight IDs; whether an
insight's *cited* `evidence_ids` are themselves citable is a T7 decision that this phase must not
pre-empt — it is recorded as open question Q1 (§9) and handed to T7 alongside ADR-14.8.

**Docs to update:** 06 (family table), 03 (§6 trace contract note).

---

### 4.11 A one-shot retroactive pass — `cli/consolidate.py` *(resolves G1)*

**Decision.** Ship `python -m cli.consolidate [--user <id> | --all] [--dry-run]`, mirroring
[cli/backfill.py](cli/backfill.py)'s composition-root shape: iterate every consolidatable series for
the target user(s), run the same `ConsolidationService` the ingest path uses, report a summary.
Idempotent by §4.6 — a second run writes zero rows.

**Why this is mandatory, not optional.** Consolidation is event-driven (ADR-3): it fires on ingest.
Replay finished on 2026-08-02 and is idempotent, so re-running it processes nothing. Therefore
**without this command the mature account has zero insights when Phase 5 ships**, and five things
break: the money shot's *"had already flagged it"* clause, the top-bar insight count, the insights
pane, the lineage graph, and the timeline's change markers. This gap is absent from the roadmap,
T5/T6, and every design doc — it is the review's clearest scope finding.

**Honesty.** Insights produced by this pass get truthful `created_at = now` and are framed in
**event time** — *"this pattern emerged in your May–June data"* — never *"flagged on 12 May"*. That
is [ADR-13.10](../office-hours/09-decisions.md#adr-13) applied verbatim; the "flagged the moment it
happened" language belongs exclusively to the live beat (§4.1).

**Rejected alternatives:** re-running replay with a consolidation flag (replay is idempotent and
would skip; it is also a *dev-time operator tool for ingestion*, and widening its job blurs
ADR-15.1); back-dating `created_at` (a replay clock — explicitly rejected by ADR-13.10); doing it
by hand in SQL (raw SQL seeding is banned by ADR-4).

**Long-term consequences.** A supported operator command exists for "re-derive everything", which is
also the recovery path if a detector bug ships: fix, re-run, and §4.6's fingerprint comparison
supersedes the wrong claims into a visible chain rather than hiding them.

**New invariants:** I-18 (retroactive insights use truthful `created_at` + event-time framing).

---

### 4.12 An insight's `event_time`, and its honesty fields *(resolves R13)*

**Decision.** `event_time = window_end` (the last moment the claim is about). The payload carries
`window_start` / `window_end` explicitly; `created_at` stays truthful. `confidence` is the
**minimum confidence across the insight's evidence**, so a claim derived from reconstructed rows
inherits their uncertainty and is discounted by assembly's existing `_PROVENANCE_FACTOR`.
`provenance` is `reconstructed` if any evidence row is, else `live`.

**Why.** Every memory needs an `event_time`, and no document defines it for insights. `window_end`
places the marker where the claim's evidence ends — correct for the timeline strip, and it makes
`get_timeline` order insights sensibly among the events they summarise. Deriving confidence from
evidence rather than from the score keeps `confidence` meaning the same thing across both memory
tiers, which is what the glass box's confidence column asserts.

**Amended 2026-08-04 (M4) — the payload also carries `pre_value` and `post_value`, typed.** §4.14's
direction-only retraction condition measures counterexamples "relative to the insight's post-shift
level", and that value was computed at derivation, consumed by the fingerprint, and then discarded —
leaving the variant unevaluatable. They are **typed fields, not `extra="allow"` extras**, because the
evaluator branches on `post_value`, which is the definition of a hot field under ADR-13.6; an untyped
extra could arrive as a string and make the comparison non-deterministic. They also make an insight
self-describing: without them the numbers exist only inside `hypothesis`, which no deterministic code
may parse (**I-8**). *(Rejected: reading the post-side value out of `evidence_ids` ordering — an
undocumented internal detail of `Finding`; re-deriving the claim — unavailable exactly when
retraction matters most, which is when the detector now refuses.)*

**Rejected alternatives:** `event_time = created_at` (puts a claim about May in August on the
timeline); `event_time = window_start` (a marker before most of its own evidence); `confidence =
pattern_strength` (conflates "how sure are we of the inputs" with "how strong is the pattern" — two
different honesty signals that judges will read as one).

**Docs to update:** 04 (insight payload example).

---

### 4.13 The pattern-strength formula *(resolves C1)*

**Decision.** One formula shape, three published factors, per-kind definitions, all in `[0, 1]`:

```
pattern_strength = effect × coverage × specificity
```

| Factor | `level_shift` | `intervention_outcome` |
|---|---|---|
| **effect** | `min(1, |post − pre| / full_delta)` | same, over the two measurements |
| **coverage** | fraction of the compared windows' days holding ≥1 contributing memory | fraction of the interval's days holding ≥1 behavioural memory |
| **specificity** | `1 / n_concurrent`, where `n_concurrent` counts other detected changes within ±`CONCURRENCY_DAYS` (3) | `1 / n_interventions` in the interval, after merging interventions closer than `MIN_INTERVENTION_GAP_DAYS` |

All three components are **stored on the insight and rendered by the UI**, so the arithmetic is
inspectable rather than a bare number. Constants are module-level, documented, and fixture-pinned.

**Amended 2026-08-04 (M2 → M3 boundary) — `effect` is measured against a per-series scale, not
against the baseline.** The original denominator made `effect` a *relative* change, and M2's
implementation showed that cannot work across these metrics: a marker that moves multiples
(vitamin D, 6.2 → 38.4, a 5.2× move) and a physiologically bounded quantity (body fat, weight)
are not the same kind of number. Under one relative floor a **3.2-point body-fat drop scored
0.082 and was refused**, while the money question scored 1.0 — so the live demo beat could never
fire, and a per-metric *floor* alone would have fixed the gate while still publishing that real
change as `pattern_strength 0.08` beside a diet change at 0.84.

Every consolidatable series therefore declares an **`EffectScale`** in its own units (§4.4):

| Field | Question | Used for |
|---|---|---|
| `min_delta` | is this bigger than our measurement noise? | the **gate** — replaces `MIN_EFFECT` |
| `full_delta` | what does a full-size change look like? | the **denominator** of `effect` |

`coverage`, `specificity`, and the product are unchanged. The global `MIN_EFFECT` and the
zero-baseline epsilon are **removed** — one mechanism, expressed in units a reader can check
("we won't claim a body-fat change under 1.5 points, because the scale isn't that precise").
The values are **product heuristics, not clinical thresholds** (ADR-13.12, I-2): they state what
*this application* treats as meaningful, each carrying its basis inline in
`engine/insights.py`. *(Rejected: a per-metric relative floor — fixes the gate, leaves the
score dishonest; one absolute floor for all series — the units differ; z-scores against series
variance — needs per-user distribution estimation, is unstable as data arrives, and drifts back
toward the statistical machinery §4.3 removed; borrowed clinical cutoffs — implies an authority
the engine does not have.)*

**Why "specificity" replaces "lag consistency".** ADR-13.12's third factor assumed a lag-correlation
detector that §4.3 removes. Specificity generalises it honestly: it asks *how uniquely this claim's
evidence points at one explanation*, and it is the factor that keeps `intervention_outcome` from
reading as causal — when several things changed at once it collapses and the insight openly says
so. **Worked example, measured against the real data (M2):** the Vitamin D recovery scores
`effect 1.000 × coverage 0.970 × specificity 0.250 = 0.242` — a strong observation with weak
attribution, which is exactly what a careful clinician would say. *(This paragraph originally
estimated specificity at ≈0.14 from seven raw interventions. The merge rule below is what the
implementation applies, and those eight interventions cluster into **four**, giving 0.25. The
formula behaved as written; the illustration predated the merge.)* The live body-scan beat scores high because exactly one intervention
falls in its interval.

**Rejected alternatives:** per-kind unrelated formulas (two scales the UI cannot compare); a
weighted sum (a weak factor gets masked — the product is what makes "one factor is bad" visible);
calibrating to a probability (ADR-13.12 forbids it outright).

**ADRs to update:** ADR-13.12 amendment.
**New invariants:** I-19 (`pattern_strength` is always published with its components; never
labelled a probability or a confidence).

---

### 4.14 Typed retraction conditions and the evaluator *(resolves C2, C3; T5)*

**Decision — schema.** ADR-13.11's `{metric, comparator/direction, window_days, min_count}` is
pinned as:

```python
class RetractionCondition(MemoryPayload):
    metric: str                          # a CONSOLIDATION_SERIES key
    direction: Literal["rising", "falling"]   # the movement that would contradict the claim
    threshold: float | None = None       # optional absolute bound; the "comparator" half
    window_days: int                     # trailing window, ending now
    min_count: int                       # counterexamples required to retract
```

**Decision — "counterexample", defined exactly.** For each observation of `metric` whose
`event_time` falls in the trailing `window_days`:

- if `threshold is None`: it is a counterexample when it moves in `direction` **relative to the
  insight's post-shift level** (`level_shift`) or **relative to the later measurement**
  (`intervention_outcome`);
- if `threshold is not None`: it is a counterexample when it crosses `threshold` in `direction`.

`count ≥ min_count` ⇒ `status = 'retracted'`. **Never deleted** (ADR-9). Evaluation rides the same
(F₀) pass, is pure arithmetic over typed fields, and involves no model.

**Prose is rendered from the object**, never stored as the condition: *"I'll withdraw this if
protein drops below 45 g/day on 3 or more days in any 30-day window."*

**Why.** ADR-13.11 left "comparator/direction" ambiguous and never defined "counterexample" — both
are load-bearing for a *deterministic* evaluator, which is the entire point of typing the condition.
Making `threshold` optional resolves the ambiguity without choosing one of the two readings: the
comparator exists when there is something to compare against, and direction alone otherwise.

**Clarified at M4 — counterexamples are counted as distinct local days.** Two contradicting meals
on one day are one counterexample. That is what makes the count mean the same thing as the sentence
the user was shown ("on 3 or more **days** in any 30-day window"), and ADR-13.11's whole premise is
that the displayed rule and the evaluated rule are one rule. This is the single place the pass counts
days where the *detectors* count assertions (**I-4**), and the distinction is intentional: I-4
protects effect size from over-weighted evidence, while a retraction asks how many days reality
disagreed — and an assertion covering thirty days really does assert about each of them.

**The write primitive is `mark_retracted`, deliberately not `mark_superseded`.** Nothing *replaces* a
retracted claim, so `superseded_by` stays NULL and the two mechanisms [ADR-9](../office-hours/09-decisions.md)
distinguishes stay distinguishable in the data. It is a single scoped UPDATE touching `status` on
active rows only — so the payload is never rewritten (**I-20**) and a second pass is a no-op rather
than a second flip. This is not a third *ingestion* transaction shape (**I-11**): it writes no
memory, and I-11 governs claim replacement, which §4.6 already handles with insert+supersede.

`now` is injected rather than read inside, so a trailing window is never anchored to a hidden clock;
the counting primitive itself takes no clock at all, since the window is applied by the query.
Conditions that cannot be evaluated — a missing `post_value`, a metric the engine cannot read — are
**skipped with a reason and leave the insight active**. Guessing a reference would retract a claim on
arithmetic the user never agreed to.

**Rejected alternatives:** LLM-evaluated prose conditions (ADR-13.11 already rejected these:
nondeterministic and budget-hostile); a general expression language (an interpreter below the
tool-call boundary).

**ADRs to update:** ADR-13.11 (refinement — pinned schema + counterexample definition).
**New invariants:** I-20 (retraction never deletes and never rewrites), I-21 (retraction evaluation
contains no model call).

---

### 4.15 Minimum-evidence thresholds: the engine **refuses to emit** *(resolves R12)*

**Decision.** Documented constants, checked before any `Finding` is produced; failure produces no
insight and a logged refusal reason (which is itself useful glass-box material later):

| Constant | Value | Applies to |
|---|---|---|
| `EffectScale.min_delta` | **per series**, in the metric's own units (§4.13 amendment) | both |
| `MIN_SPAN_DAYS` | 7 | `level_shift` — each compared side |
| `MIN_INTERVAL_DAYS` | 14 | `intervention_outcome` |
| `MIN_MEASUREMENTS` | 2 | `intervention_outcome` |
| `MIN_INTERVENTIONS` | 1 | `intervention_outcome` |
| `CONCURRENCY_DAYS` | 3 | `level_shift` specificity |
| `MIN_INTERVENTION_GAP_DAYS` | **3** *(set at M2 — §4.13 named the constant without a value; matched to `CONCURRENCY_DAYS` so "the same moment" means the same span on both detectors)* | `intervention_outcome` specificity |

**Why.** With one historical body scan, a second scan creates a two-point series — and a product
that emits "a pattern" from two adjacent points in front of judges has fabricated one. A refusal is
a feature: it is what makes the insights that *do* appear worth believing, and it is the mechanical
expression of ADR-13.12's "labeled heuristic, never a probability". Thresholds live as constants
because a per-user or adaptive threshold would be untestable and unexplainable.

**Rejected alternatives:** emit-with-low-score (a 0.02 pattern strength still renders as an insight
card and still gets cited); no threshold (the demo becomes an argument about statistics).

**New invariants:** I-22 (a detector that cannot clear its thresholds emits nothing — silence is a
valid, tested outcome).

---

### 4.16 Insights in the existing read paths *(resolves R5)*

**Decision.**

1. `_TIER_SCORE["insight"] = 1.0` stays, but the **per-type diversity cap already caps insights at
   `_DEFAULT_PER_TYPE_CAP = 5`** of the 12-row narration budget. No new mechanism; the existing cap
   is the defence, and a test pins it.
2. `get_timeline` and `recall_memories` continue to return insights when unfiltered — that is 06's
   intended "secondary discovery path" — and each gains an explicit test rather than remaining
   incidental behaviour.
3. Default `status='active'` filtering already excludes retracted and superseded insights in every
   builder. A `status` slot for deliberate retracted-insight browsing is **Phase 6**, not now.

**Why.** All three behaviours are already implemented and untested for insights; the risk is not
that they are wrong but that they are unverified at the moment insights first exist. Adding tests is
cheaper and less invasive than adding machinery.

**New invariants:** none — this pins existing behaviour.

---

### 4.17 Photo ingestion, and never-lose-input for a photo *(resolves R10)*

**Decision.** Photo ingestion lands as an **independent lane (M7), first-to-cut**, and closes the
never-lose-input hole explicitly:

- `ModelProvider` gains `extract_from_image(s3_key, *, now, tz, caption) -> list[ExtractedEvent]`
  with the same three-outcome contract as `extract_events` (typed events / affirmed-empty /
  raise).
- `NotePayload.text` stays required; a photo turn that fails vision writes a note whose `text` is
  the user's caption when present, and otherwise the **honest literal** `"[photo, not parsed]"`,
  with `photo_s3_key` carried as an extra payload key. The image itself is already durable in S3
  before extraction runs, so nothing is lost.
- S3 failure ⇒ the turn still persists as a text/note turn with a clear partial-save message
  (the 12-test-plan row).
- The deferred **note-confidence** TODO closes here: `_NOTE_CONFIDENCE` becomes a parameter of the
  note path, defaulting to 1.0 for live chat, so a photo/vision fallback can carry a lower value.

**Why.** Upload-then-extract makes S3 the durability boundary for the bytes and keeps the memory
row's guarantee unchanged. A required-`text` note with no text is the only structural hole, and a
literal marker is more honest than making the field optional (which would let a *text* turn write a
textless note).

**Rejected alternatives:** making `NotePayload.text` optional (weakens the guarantee for the path it
was written for); extract-then-upload (a vision failure loses the photo); skipping the note on a
photo failure (silently drops input — the one thing ADR-13.5 forbids).

**Docs to update:** ingestion-transaction-boundaries.md (§4 photo branch, §9 matrix), TODOS.md
(close the note-confidence entry).
**New invariants:** I-23 (a photo turn persists something for every outcome).

---

### 4.18 Scope calls: canonicalization and period-aware aggregation stay out *(resolves C7, C9)*

**Decision.** Neither ships in Phase 5. Both remain recorded handoffs.

**Entity canonicalization — out.** The Temporary Architecture Decision Log's own timing argument
was *"adopting canonicalization **before** replay canonicalizes all 6–12 months free; adopting it
after means re-extracting history."* **That window closed on 2026-08-02.** Adopting it now means
either backfilling a `canonical` field into 424 committed payloads or a supersession-based re-run
(`--apply-corrections`, deliberately manual per ADR-15.5) — a materially larger job than the entry
priced, executed against the one account the demo depends on, three weeks before the deadline. The
recommended design does not need it: §4.4's supplement intervention detection uses exact
`payload.name` equality, which the converter already made consistent. `normalize_item` therefore
**stays** exactly as replay-architecture §4.13 specified it — query-time, write-path-free, non-goal
list test-enforced.

**Period-aware aggregation — out.** §4.2 captures most of its value inside the consolidation reader
without touching a single Phase 3 builder. Doing it properly modifies `aggregate_memories` behind
498 tests and requires solving the double-count question replay-architecture §8 posed and did not
answer. Unchanged verdict: deferred on scope, not merit.

**Note-confidence threading — in**, but only as part of M7 (§4.17), which is where it becomes
reachable.

**Long-term consequences.** The `"when did I last eat chicken?"` vs `"Grilled Chicken"` miss
documented in replay-architecture §8 persists through the hackathon, mitigated by the planner's
existing instruction to pair `lookup_events` with `recall_memories`. That is an accepted, documented
limitation with an honest answer available if a judge finds it.

---

## 5. Invariants this phase introduces

Numbered so tests and code comments can cite them.

| # | Invariant |
|---|---|
| **I-1** | An insight is one of the registered kinds; adding a kind is a reviewed, ADR-recorded act. |
| **I-2** | No insight is ever presented as causal, as a probability, or as a confidence interval. |
| **I-3** | The live "flagged the moment it happened" framing is used **only** where `created_at = now` and the evidence is live (ADR-13.10). |
| **I-4** | Consolidation observes **assertions**, not materialized period days. |
| **I-5** | An insight's lineage is boundary-anchored and capped, and always publishes `evidence_count`. |
| **I-6** | Daily bucketing leaves gaps missing. Health data is never interpolated. |
| **I-7** | Detectors are pure: no I/O, no clock, no model, no global state. |
| **I-8** | Interventions are detected structurally. The engine never reads note prose. |
| **I-9** | `CONSOLIDATION_SERIES` is a closed vocabulary and a strict subset of `METRICS`. |
| **I-10** | At most one **active** insight exists per `(user_id, kind, series_key)`. |
| **I-11** | No third ingestion transaction shape. Insight updates reuse insert+supersede. |
| **I-12** | Re-running consolidation over unchanged data writes **zero** rows. *(the phase's load-bearing property test)* |
| **I-13** | `memories` mutations remain limited to `status`, `superseded_by`, `embedding`. |
| **I-14** | Consolidation never runs inside the turn's write transaction. |
| **I-15** | A consolidation failure never fails a turn. |
| **I-16** | Insights are written with `embedding = NULL` and embedded by the existing backfill. |
| **I-17** | The closed retrieval builder set stays **read-only**. Writers are graph-dispatched. |
| **I-18** | Retroactively derived insights carry truthful `created_at` and event-time framing. |
| **I-19** | `pattern_strength` is always published with its three components. |
| **I-20** | Retraction flips `status`; it never deletes and never rewrites a payload. |
| **I-21** | Retraction evaluation contains no model call and no language. |
| **I-22** | A detector that cannot clear its thresholds emits nothing. Silence is a tested outcome. |
| **I-23** | Every photo turn persists something, for every failure outcome. |
| **I-24** | Every consolidatable series declares an `EffectScale`; a series without one cannot be consolidated. There is no global effect floor to fall back on. |

**Carried forward unchanged from earlier phases** (must stay green): all six
ingestion-transaction-boundary rules; never-lose-input; the shared (B)–(F) tail is not forked; the
M5-1 checkpoint durability guard and channel allowlist; per-user scoping in every query;
`status='active'` default filtering; row-at-a-time inserts (C-SPANN); replay's zero-extraction
property; the ledger's post-commit ordering; `expanded_from` + lowered confidence on every synthetic
row; `--apply-corrections` stays manual; `normalize_item` stays query-time; the two-cluster test
rule; 498 tests green at every milestone boundary.

---

## 6. Documents and ADRs that must change

| Document | Change | When |
|---|---|---|
| **09-decisions.md — ADR-13.12** | **Amendment.** Detector set: `ruptures` PELT and the 7–35 day lag scan are removed (§4.3); `level_shift` + `intervention_outcome` replace them; pattern-strength third factor renamed *lag consistency → specificity* (§4.13). | M0 |
| **09-decisions.md — ADR-13.11** | **Refinement.** Pinned `RetractionCondition` schema; "counterexample" defined (§4.14). | M0 |
| **09-decisions.md — ADR-13.1** | **Amendment.** Consolidation's stage placement is (F₀), post-commit, best-effort (§4.8). Budget value re-derived by T12. | M0 draft; number finalised M6 |
| **09-decisions.md — ADR-16 (new)** | Phase 5 decisions §4.1–§4.18, in the ADR-15 style: only what is architecturally binding and what implementation validated. | promoted at phase close |
| **06-retrieval-strategy.md** | Insight-reuse section drops `last_evaluated_at` for derived freshness (§4.7); builder-family table gains the insight family (§4.10) and the marker metrics (§4.5). | M1 / M5 |
| **04-database-design.md** | Insight payload example refreshed (kinds, window, components, pinned retraction condition); insight `event_time` semantics (§4.12). | M1 |
| **03-memory-engine.md** | §4 consolidation updated to the two kinds; tool table notes `analyze_series` is graph-dispatched (§4.9); §6 notes lineage arrives via the insight family (§4.10). | M5 |
| **05-agent-architecture.md** | Mermaid gains the consolidate node; model-surfaces table gains the vision surface. | M5 / M7 |
| **ingestion-transaction-boundaries.md** | §4 gains stage (F₀) and the photo branch; §9 matrix gains consolidation + vision rows; §12 notes (F₀) sits outside the T7 turn transaction. | M5 / M7 |
| **replay-architecture.md** | §8 handoffs annotated: 1 deferred (§4.18), 2 partially answered (§4.2), 4 closed by M7. | phase close |
| **12-test-plan.md** | Consolidation block expanded to the tests in §9. | each milestone |
| **implementation-roadmap.md** | Phase 5 deliverables gain `cli/consolidate.py` (§4.11) and record the scope calls. | M0 |
| **TODOS.md** | Close the note-confidence entry (M7); fold the fixture-cleanup entry into M0. | M0 / M7 |

---

## 7. Risk analysis

**[P0 — correctness] Duplicate insights.** The Phase-5 analogue of Phase 4's duplicate-memory risk:
consolidation runs on every ingest, and the write path does not deduplicate. *Mitigated by* §4.6's
identity rule and **I-12**, which gets the same treatment Phase 4 gave the forced-double-run test —
a load-bearing property test, not an assertion buried in a unit test.

**[P0 — honesty] A confident score on a synthetic artifact.** §4.2's assertion-level read is the
mitigation; without it, a `pattern_strength ≈ 1.0` would be published for the converter's own
segment boundaries in the panel judges are invited to click into.

**[Correctness] The boundary the notes tempt you to cross.** §3.3's notes read like an intervention
log. Mining them is the single most likely boundary violation in this phase and would silently
convert the deterministic layer into an NL interpreter. *Mitigated by* **I-8** plus a test asserting
no note text reaches a detector.

**[Schedule/latency] The 300 ms budget may be unachievable cross-region.** ~200–250 ms per round
trip, 2–3 trips needed. *Mitigated by* §4.4 (one series per meal), §4.8 rule 3 (no embed call), and
T12 measuring before anyone claims a number. Worst case is an honest ADR-13.1 amendment, not a
broken feature — the deferral path already exists and ingestion is unaffected.

**[Product] Deferred work may never execute.** Budget overflow defers to `analyze_series`, which
only runs if a user asks. *Mitigated by* §4.7's derived freshness (the recompute happens on the next
ingest touching the series anyway) and by wording 06 honestly instead of claiming currency "by
construction".

**[Cost] Consolidation multiplies per-ingest database work.** Every meal now triggers a series read
plus an identity check. Unmeasured. *Mitigated by* the single-series rule and M6's measurement;
mitigation available if it bites — trigger consolidation only when the ingested row is the newest in
its series.

**[Test infrastructure] Consolidation tests need series.** Tens of rows per test against a shared
cluster whose fixtures never clean up (TODOS). Phase 4 grew the suite from 359 to 445 and the
previous cluster died at 4,690 users. *Mitigated by* making fixture cleanup part of M0 rather than a
follow-up.

**[Scope] Photo ingestion is a second AWS surface** (bucket, IAM task-role policy, presigned upload)
with no consolidation value. It is the correct first cut, and M7 is positioned last for exactly that
reason.

**[Accepted, bounded] Supplement interventions key on exact `payload.name`.** Two spellings read as
two onsets. Consequence of §4.18's canonicalization deferral; bounded by the intervention-merge
window; documented rather than eliminated.

---

## 8. Milestone plan

Eight milestones, each independently reviewable and commit-worthy. Order is *pure code → database →
live write path → measurement → orthogonal lane*, the same shape that made Phase 4 safe.

| | Milestone | Depends on | Commit |
|---|---|---|---|
| M0 | Decisions locked + fixture hygiene | — | `docs: lock the Phase 5 consolidation architecture (ADR amendments)` |
| M1 | Insight contracts *(pure)* | M0 | `feat(engine): typed insight payload + retraction condition schema (T5)` |
| M2 | Analytics kernel *(pure)* | M1 | `feat(engine): deterministic level-shift + intervention-outcome detectors (T6)` |
| M3 | Consolidation service + identity rule | M2 | `feat(engine): consolidation service — series read, identity, supersession (T6)` |
| M4 | Retraction evaluator | M1, M3 | `feat(engine): typed retraction evaluation (T5)` |
| M5 | Wiring + retroactive pass | M3, M4 | `feat(engine): sync consolidation on ingest, analyze_series, insight lineage` |
| M6 | Latency profile | M5 | `docs: measured turn-latency profile (T12)` |
| M7 | Photo ingestion *(first to cut)* | M1 | `feat(engine): photo ingestion — S3 + vision → meal events` |

---

**M0 — Decisions locked + fixture hygiene** *(no feature code)*
- **Objective:** this document approved; the ADR amendments in §6 written; the test-fixture cleanup
  landed so the cluster survives the phase.
- **Files:** this document, `09-decisions.md`, `implementation-roadmap.md`, `TODOS.md`,
  `engine/tests/conftest.py`.
- **Invariants:** the two-cluster rule survives any conftest change; no §4 decision is taken
  silently.
- **Tests:** session-scoped cleanup fixture; assert a full run leaves no residue for the UUIDs it
  minted.
- **Rollback:** docs-only; revert the conftest commit.

**M1 — Insight contracts** *(pure, no DB)*
- **Objective:** full `InsightPayload` (kind, series key, window, evidence + count, three strength
  components, retraction condition, fingerprint), `RetractionCondition`, `SeriesKey`,
  `CONSOLIDATION_SERIES`, the five marker metrics, the condition→prose renderer.
- **Files:** `engine/types.py`, `engine/insights.py` *(new)*, `engine/retrieval.py` (METRICS only),
  `engine/tests/test_insight_types.py`.
- **Invariants:** I-1, I-9, I-19; `extra="allow"` preserved; drift canary green; no model anywhere.
- **Tests:** drift canary extended; every retraction-condition shape round-trips; prose renderer is
  deterministic and never stored as source of truth; fingerprint is stable under prose rewording and
  changes under a value change; the marker metrics resolve to correct JSONB paths.
- **Rollback:** additive optional fields — one clean revert.

**M2 — Analytics kernel** *(pure, no DB, no model)*
- **Objective:** observation collapse (§4.2), both detectors, the strength formula, the thresholds.
- **Files:** `engine/analytics.py` *(new)*, `engine/tests/test_analytics.py`.
- **Invariants:** I-2, I-4, I-6, I-7, I-19, I-22.
- **Tests:** the **real protein series** (§3.2) as a fixture → three level shifts with expected
  boundaries and components; the real Vitamin D pair → one `intervention_outcome` with the expected
  three factors; series *without* a shift → nothing; a 2-point series below `MIN_INTERVAL_DAYS` →
  nothing; gap-heavy series never interpolates; 30 expanded rows collapse to 1 observation; strength
  components pinned numerically; **no note text can reach a detector** (I-8).
- **Rollback:** leaf module, nothing imports it — delete.

**M3 — Consolidation service + persistence**
- **Objective:** read a user-scoped series, collapse, detect, apply the identity rule, persist via
  insert+supersede, enforce the deadline budget.
- **Files:** `engine/consolidation.py` *(new)*, `engine/repository.py`,
  `engine/tests/test_consolidation.py`.
- **Invariants:** I-5, I-10, I-11, I-12, I-13, I-16; every query filters `user_id`; row-at-a-time
  inserts.
- **Tests:** **PROPERTY — re-running over unchanged data writes zero rows (I-12)**; changed data →
  replacement inserted + prior `superseded_by` set, one transaction; detector refusal leaves an
  existing insight active; budget exceeded → clean stop, nothing half-written; cross-user isolation;
  insights land with `embedding IS NULL`.
- **Rollback:** not yet called from ingestion — dead code until M5.

**M4 — Retraction evaluator (T5)**
- **Objective:** deterministic condition evaluation in the same pass; `status='retracted'`.
- **Files:** `engine/consolidation.py`, `engine/repository.py`, `engine/tests/test_retraction.py`.
- **Invariants:** I-20, I-21.
- **Tests:** condition met → flip, row still present, payload untouched; not met → untouched;
  `threshold` and `direction`-only variants; supersession chain traversable; a retracted insight
  disappears from default retrieval but is fetchable deliberately.
- **Rollback:** independent of M3's call site.

**M5 — Wiring + retroactive pass** *(the risky milestone)*
- **Objective:** (a) stage (F₀) hook in the ingestion tail with receipt extension; (b)
  `analyze_series` graph-dispatched; (c) the insight builder family; (d) `trace.insights` populated;
  (e) `cli/consolidate.py`.
- **Files:** `engine/ingestion.py`, `engine/assembly.py`, `engine/retrieval.py`, `agent/tools.py`,
  `agent/graph.py`, `agent/providers/_prompts.py`, `api/routers/ingest.py`, `cli/consolidate.py`
  *(new)*.
- **Invariants:** I-14, I-15, I-17, I-18; all six transaction-boundary rules; never-lose-input; the
  shared tail is not forked; `assemble()` stays pure (ADR-14.7); no heavy insight object enters
  `GraphState` (M5-1 — extend `_BANNED_STATE_TYPES` if a new heavy type appears).
- **Tests:** **the full 498-test suite stays green**; ingest → insight appears in the receipt;
  consolidation raising → turn still succeeds with a correct receipt; `analyze_series` never executes
  inside the retrieve transaction; `trace.insights` carries correct lineage; `citable_ids` includes
  insight IDs; `cli/consolidate.py` is idempotent (second run → 0 new) and `--dry-run` writes
  nothing; per ADR-15.6, **at least one assertion at the committed-row layer**, not only at the unit
  that owns the logic; a 10-series smoke run before the full sweep.
- **Rollback:** the (F₀) hook is one call site behind a config toggle; the CLI is a leaf.

**M6 — Latency profile (T12)**
- **Objective:** measure ingest / query / both turns against the **deployed** cross-region topology;
  write `docs/latency.md`; confirm or amend ADR-13.1's budget honestly.
- **Files:** `docs/latency.md` *(new)*, light timing instrumentation.
- **Invariants:** no infrastructure built solely for completeness; measurement must not alter the
  measured path.
- **Tests:** none required — the artifact is the deliverable.
- **Rollback:** docs-only.

**M7 — Photo ingestion** *(orthogonal; first to cut)*
- **Objective:** presigned S3 upload → Bedrock vision → meal events; every failure outcome persists
  something.
- **Files:** `engine/model.py`, both providers + `CompositeProvider`, `engine/ingestion.py`,
  `api/routers/ingest.py`, IAM/task-role, `Dockerfile`.
- **Invariants:** I-23; never-lose-input extended, not weakened; `_NOTE_CONFIDENCE` parameterised.
- **Tests:** vision success → typed meal with `photo_s3_key`; vision failure → note with the honest
  literal + the S3 key; S3 failure → turn persists with a clear partial-save message; the
  three-outcome provider contract holds for the vision surface.
- **Rollback:** additive route + provider method — delete.

---

## 9. Test strategy

Beyond each milestone's own block, four tests carry the phase:

1. **I-12 — recompute writes zero rows.** The load-bearing property, the direct analogue of Phase
   4's forced-double-run guard. Run it at the **committed-row layer** (count rows before/after),
   not against the service's return value.
2. **I-8 — no note prose reaches a detector.** A fixture with a note whose text would obviously
   suggest an intervention, asserting the detector's intervention set does not contain it.
3. **The real-data fixtures.** §3.2's protein series and the real Vitamin D pair, checked into
   `engine/tests/fixtures/` **in sanitized form** (ADR-7 — the raw reconstruction stays local). These
   are what make M2 a test of the product rather than of a synthetic curve.
4. **The full suite stays green at every milestone boundary.** 498 today; Phase 5 must never leave
   it red between commits.

Seam coverage follows ADR-15.6's lesson explicitly: M2→M3 (does the service pass the detector what
the detector's own tests assume?) and M3→M5 (does the receipt carry what the service committed?) get
their own assertions at the outermost observable layer.

---

## 10. Open questions

| # | Question | Owner |
|---|---|---|
| **Q1** | Are an insight's own `evidence_ids` citable by the narrator, or only the insight's ID? Widens `citable_ids` and interacts with ADR-14.8. **Do not pre-empt** — hand to T7 with the ADR-14.8 decision. | T7 / Phase 6 |
| **Q2** | Does the Phase 6 UI render `evidence_count` beside a capped lineage? Required for §4.2 to stay honest on screen. | Phase 6 |
| **Q3** | After M6's measurement, does ADR-13.1's budget number change, and does the deferral path need a catch-up trigger? | M6 |

**`ruptures` re-add trigger and recipe** *(recorded per the ADR-15.2 precedent)*: reinstate PELT when
a **single user's single metric** accumulates ≥ 60 genuinely observed (non-`expanded_from`) daily
values. Recipe: add `ruptures` to dependencies, implement `detect_changepoints(obs, …) -> list[Finding]`
behind the existing detector interface, register it for `kind='behavioural'` series that clear the
observation threshold, and add it to `CONSOLIDATION_SERIES`' per-metric detector map. Nothing else
changes — persistence, identity, retraction, retrieval, and the strength formula are detector-agnostic
by construction. **Not a trigger:** a desire to say "changepoint detection" in the README.

---

## 11. Implementation record (M0 → M5a)

> Decisions and findings that came out of *building* this, not out of designing it. §4 says what
> the architecture is; this section says what implementation taught, so a future agent can tell a
> deliberate choice from an accident. Everything here was approved before it landed.

### 11.1 Choices §4 left open, settled in code

| # | Decision | Where | Why |
|---|---|---|---|
| a | **`intervention_outcome` compares the two most recent measurements**, not first-to-last | M2, `engine/analytics.py` | §4.1 says a claim arises when a marker "gains a second (or later) measurement". Comparing each new measurement against its predecessor keeps the interval — and so the intervention set inside it — local and defensible. First-to-last would sweep every intervention an account ever saw into one claim and drive specificity toward zero as history grows: the wrong answer getting more wrong over time. |
| b | **`MIN_INTERVENTION_GAP_DAYS = 3`** | M2 | §4.13 named the constant without a value; matched to `CONCURRENCY_DAYS` so "the same moment" spans the same window on both detectors. |
| c | **`level_shift` specificity is scoped to its own series** — other shifts found in the same run, within `CONCURRENCY_DAYS` | M2 | A single-series detector cannot see other series. Cross-series attribution is `intervention_outcome`'s question, and is exactly where the score correctly collapses. |
| d | **One finding kept per series** (the most recent boundary) | M3, `_detect` | Identity is `(user, kind, series)`, so a series holds one active claim. Earlier shifts are not lost — each remains available as an *intervention* for the outcome detector. |
| e | **`ONSET_SOURCES = {supplement: by exact name, workout: first-ever}`** | M3 | §4.4 defines onsets structurally but names no types. These are the two the data has. Exact-name matching is where §4.18's canonicalization deferral is felt: two spellings read as two onsets — bounded and documented. |
| f | **`BEHAVIOURAL_TYPES = (meal, workout, sleep, supplement)`** for coverage | M3 | §4.13's coverage asks "was the engine watching"; a second blood panel is not evidence anyone was logging behaviour. |
| g | **`source='consolidation'`** on every derived row | M3 | Lets the glass box, and any later audit, tell a claim the engine made from a fact the user reported. |
| h | **A surplus of active insights is reconciled and logged**, never silently resolved by picking a winner | M3, `_apply` | A duplicate nothing complains about is how the count drifts. |
| i | **`engine/insights.py` must not import `engine/retrieval.py`** | M1 | `CONSOLIDATION_SERIES` is a subset of `METRICS` (**I-9**), but importing it would invert the layering the moment M5b's insight builder family makes `retrieval` need these contracts — a cycle created by one line and paid for two milestones later. I-9 is enforced by a test that imports both, the same posture as the payload registry's drift canary. |
| j | **`pattern_strength` must *equal* its components**, checked at the payload boundary within `STRENGTH_TOLERANCE` | M1, `engine/types.py` | A strengthening of **I-19** beyond "published with". A score its components cannot explain is the unfalsifiable number ADR-13.12 exists to prevent, and the payload boundary is the only place no code path can route around. |
| k | **`consolidation` is an optional dependency of `IngestionService`** | M5a | Every pre-Phase-5 caller and test constructs the write path without one and behaves identically — which is what keeps 600+ existing tests meaningful. |
| l | **The receipt keeps the two tiers apart** (`created` vs `insights`); the API key is additive | M5a | `created` is what the user reported; an insight is a claim *about* it. One list would let a receipt imply the user logged something they never said. |
| m | **The insight family is read-only and does no freshness check** | M5b | Recomputing a stale claim is a write; `analyze_series` owns it (I-17). |
| n | **Insights bypass the raw-event budget**, and carry a `ContextBlock.insights` field | M5b | 06's tier axis applied at the budget: the claim that answers the question must not be crowded out by the events it summarises. The field is also what lets the narrator see — and therefore cite — them. |
| o | **On a duplicate, the lineage-carrying row wins** over the payload-free snapshot | M5b | 06: one memory is one candidate. Only the family's row carries `evidence_ids`. |
| p | **`citable_ids()` gains insight ids but not their `evidence_ids`** | M5b | Q1 is T7's to answer; a surface is easier to widen than to narrow. Asserted by test so widening is deliberate. |
| q | **The insight tool takes no date range** | M5b | An insight carries its own window; a planner range would filter by *when it was derived* rather than what it is about. |
| r | **One `_STAGES` table drives every routing edge** (ingest → consolidate → retrieve) | M5c | A hand-written edge per predecessor pair lets a stage be reachable from one and unreachable from another. One table makes that impossible. |
| s | **`WRITE_TOOLS = {log_memory, analyze_series}`**, disjoint from `RETRIEVAL_TOOLS` | M5c | I-17 becomes a structural fact — the read set is read-only because the writers are not in it — rather than a rule someone must remember. `prepare_call` refuses both. |
| t | **One `ConsolidationService` instance, shared by stage (F₀) and the tool** | M5c | Constructed once in the composition root. A second instance would be a second place the identity rule lives, which is what I-12 exists to prevent. |
| u | **`--dry-run` is `ConsolidationService.analyze()`** — the same path stopped one step before the insert, not a parallel algorithm | M5d | A dry run that could disagree with the real run would be worse than none, because an operator would trust it. It even builds and validates the payload, so it cannot promise a claim the real run would reject. |
| v | **The sweep lifts the (F₀) budget** (`SWEEP_BUDGET_MS`, 10 min) | M5d | The ~300 ms budget exists to protect an interactive turn; an operator command has nobody waiting, and deferring series there would just mean an incomplete pass to run again. Bounded rather than unbounded so a stuck series still ends. |
| w | **`users_with_memories` excludes insight-only accounts** | M5d | Otherwise `--all` would grow with its own output. |

### 11.2 Consequences worth knowing before you touch this

- **`level_shift` structurally cannot fire on live daily logging.** A one-day observation has no
  *level*, so `MIN_SPAN_DAYS` refuses it and day-to-day noise can never become a claim. The live
  path is `intervention_outcome` (§4.1), which is what the demo beat uses. Discovered while writing
  M3's provenance test; intended behaviour, not a gap.
- **Refusals are emitted to a `logging` debug channel, not returned.** §4.3 pins the detector
  signature as `-> list[Finding]`, and §4.15 says a refusal is "logged". I-7's "no I/O" means no
  network, database, or filesystem — observability is not what it protects.
- **A JSON `null` is not SQL `NULL`.** The retractable-insight query filters
  `jsonb_typeof(payload -> 'retraction_condition') = 'object'`, not `IS NOT NULL`. Production rows
  omit the key entirely (`payload_to_json` drops `None`) so both agreed there, but a hand-repaired
  payload is exactly where they would not, and "no condition" must mean one thing however written.
- **No schema change was needed.** The existing inverted JSONB index serves the active-insight
  lookup; `engine/schema.sql` is untouched by Phase 5 so far.
- **Never let a test invoke a cluster-wide sweep.** `cli/consolidate.py --all` is correct for an
  operator command, but a *test* that runs it sweeps every account the suite has accumulated —
  during a full run that measured ~150 accounts × 9 series, roughly fourteen minutes inside one
  test, and it took the suite from 8 to 36 minutes. `test_all_sweeps_every_discovered_account`
  therefore stubs discovery and tests the loop; discovery gets its own single-query test. This
  is the same unbounded-sweep trap TODOS.md already records against `test_backfill.py`.
- **`SerializationFailure` has no retry path** (TODOS.md). CockroachDB's retryable error class
  reaches callers unhandled, which shows up as 1–2 non-reproducible full-suite failures per run
  at Phase 5's transaction volume. Deferred deliberately: the obvious fix is impossible (a
  `@contextmanager` cannot re-execute its caller's block), so a real fix changes the seam's
  shape and belongs in its own infrastructure change.

### 11.3 Measured findings

| Measurement | Value | Consequence |
|---|---|---|
| Consolidation cost per series, app → CockroachDB Cloud **ap-south-1** | **~635 ms** | ADR-13.1's provisional 300 ms budget completes **exactly one series** and defers the rest. The mechanism behaves correctly — clean deferral, nothing partial, no error — and the *number* is T12's to re-derive, as that amendment anticipated. |
| Full 9-series pass, same path | ~5.7 s | Why M3/M5a tests that assert *coverage* rather than speed lift the budget explicitly. |
| Real protein series → level shifts | effect 0.167 / 0.300 / 1.000 at 2026-04-25, 06-15, 06-23 | Pinned in `test_analytics.py`; a change to §4.13's arithmetic fails a test before it fails a walkthrough. |
| Real Vitamin D pair → `intervention_outcome` | effect 1.000 · coverage 0.970 · specificity 0.250 · **strength 0.242** | The money question, as arithmetic. |
| Test-suite residue per full run after M0 | `memories` 0, `users` 0, `sessions` 0 | Was +1,455 / +146 / +135. |
| Remaining residue: LangGraph checkpoint rows | **281 per run** | Agent tests use raw thread ids, so the prefix purge cannot see them. Recorded in TODOS.md, deliberately outside M0. |

### 11.4 Milestone status

| | Milestone | Status |
|---|---|---|
| M0 | Decisions locked + fixture hygiene | ✅ `ce4d961` |
| M1 | Insight contracts | ✅ `f2d109b` |
| — | EffectScale amendment (§4.13) | ✅ `0343958` |
| M2 | Analytics kernel | ✅ `b2d5b25` |
| M3 | Consolidation service + identity | ✅ `7385769` |
| M4 | Retraction evaluator | ✅ `d45ca8b` |
| — | `claim_dates` identity fix (§4.6) | ✅ `7c49123` |
| M5a | Stage (F₀) ingestion hook | ✅ `489f1cc` |
| M5b | Insight builder family + trace lineage | ✅ `af9d4a2` |
| M5c | `analyze_series` graph dispatch | ✅ `f0e76a6` |
| M5d | `cli/consolidate.py` | ✅ `748f418` |
| M6 | Latency profile (T12) | ⏳ |
| M7 | Photo ingestion | ⏳ (first to cut) |

## 12. Maintenance notes

- **Do not let a detector read text.** Every intervention must reduce to exact equality or
  arithmetic (I-8). This is the invariant most likely to be eroded by a well-meaning improvement,
  because the notes in §3.3 look so much like structured data.
- **Do not remove the assertion-level collapse** (§4.2) to "use more data". It is the only thing
  standing between the strength score and the converter's own segment boundaries.
- **Do not make `pattern_strength` a single opaque number.** The three components are what keep it a
  labeled heuristic rather than a probability (I-19, ADR-13.12).
- **Do not add a third ingestion transaction shape** (I-11). replay-architecture §4.14 already named
  this as the point to stop and ask.
- **Do not move consolidation inside the turn transaction** to make the insight atomic with the
  memories. It is derived data; losing it costs one re-derivation, and the rule it would break
  protects every write in the system.
- **Do not store `last_evaluated_at`** as a "small optimization" (§4.7). It reintroduces payload
  mutation for a derivable value and gives the same fact two sources of truth.
- **Do not lower the §4.15 thresholds to make the demo produce more insights.** Fewer, believable
  insights is the product.
- **When M1–M7 land**, promote §4 into ADR-16 in
  [09-decisions.md](../office-hours/09-decisions.md), recording only what is architecturally binding
  and what implementation actually validated — the ADR-15 discipline.

---

## 13. Related files

| File | Relationship |
|---|---|
| `engine/types.py` | `InsightPayload` + `RetractionCondition` (M1); the registry already contains `insight` |
| `engine/insights.py` *(new)* | Insight contracts, series keys, fingerprints, prose rendering |
| `engine/analytics.py` *(new)* | Pure detectors, collapse, strength formula, thresholds |
| `engine/consolidation.py` *(new)* | The service: read → detect → identity → persist → retract |
| `engine/repository.py` | Series reads, insight queries, `mark_superseded` (the existing retraction primitive) |
| `engine/ingestion.py` | Stage (F₀) hook; the shared (B)–(F) tail must not be forked |
| `engine/retrieval.py` | Marker metrics, the insight builder family; `normalize_item` stays untouched (§4.18) |
| `engine/assembly.py` | `trace.insights` populated; `_TIER_SCORE["insight"]` becomes live |
| `engine/trace.py` | `InsightRef` — already specified, finally populated |
| `agent/tools.py`, `agent/graph.py` | `analyze_series` dispatch (§4.9); `test_tools.py:185` flips meaning |
| `cli/consolidate.py` *(new)* | The retroactive pass (§4.11); mirrors `cli/backfill.py` |
| `cli/backfill.py` | Composition-root pattern; also embeds the insights M5 writes unembedded |
| [ingestion-transaction-boundaries.md](ingestion-transaction-boundaries.md) | The stage diagram (F₀) extends; rules 1–6 are unchanged constraints |
| [replay-architecture.md](replay-architecture.md) | §4.1 expansion semantics that §4.2 consumes; §8 handoffs this phase answers or defers |
| [graph-state-durability.md](graph-state-durability.md) | M5-1 boundary: no heavy insight object may enter `GraphState` |
| [09-decisions.md](../office-hours/09-decisions.md) | ADR-13.1/13.11/13.12 amendments; destination for ADR-16 |
| [12-test-plan.md](../office-hours/12-test-plan.md) | The consolidation block this phase fills in |
