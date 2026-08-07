# Engineering Deep Dives

Implementation-level documents for future maintainers (human or AI). These are neither
ADRs (decisions live in [office-hours/09-decisions.md](../office-hours/09-decisions.md))
nor user guides — each one teaches the reasoning behind a non-obvious piece of the
codebase: why it exists, how it was investigated, and how to modify it safely.

Conventions:

- One document per subsystem or investigation, written while the work is fresh.
- Documents here are the **canonical implementation reference** for their subject —
  ADRs, tasks, and code comments should link here instead of duplicating explanations.
- Each document ends with maintenance notes (when it can be removed/revisited, what not
  to touch casually) and a related-files table.

## Index

| Document | Subject |
|---|---|
| [cockroachdb-postgressaver.md](cockroachdb-postgressaver.md) | LangGraph checkpointing on CockroachDB: why stock `PostgresSaver` fails, the T2 canary investigation, and the `CockroachDBSaver` compatibility layer (`agent/checkpointer.py`) |
| [ingestion-transaction-boundaries.md](ingestion-transaction-boundaries.md) | The Phase 2 write path (`engine/ingestion.py`): transaction boundaries, the never-lose-input guarantee, and how partial extraction / validation / embedding failures interact with it (ADR-13.5) |
| [graph-state-durability.md](graph-state-durability.md) | Keeping heavyweight runtime objects out of the LangGraph checkpoint (ADR-14.9): why "clear it before END" fails, the carrier design, LangGraph's silent-drop behavior, and why the serde guard — not the state schema — is the guarantee |
| [vector-index-and-filtered-knn.md](vector-index-and-filtered-knn.md) | How semantic recall actually executes: the T1 canary proved *unfiltered* K-NN uses the vector index, but the product's user-scoped filtered query does not — the measurement, the decision to keep correctness, and the honest scale answer T17 needs |
| [cockroachdb-lessons-learned.md](cockroachdb-lessons-learned.md) | **The CockroachDB engineering record**: the 21½-minute teardown DELETE incident (symptom → wrong diagnosis → investigation → root cause), plus every CockroachDB-specific constraint this project hit — the vector index our own filters bypass, Euclidean-only C-SPANN, the C-SPANN batch footgun, LangGraph's PostgresSaver incompatibility, `schema_locked` vs `TRUNCATE`, test-data accumulation killing a cluster, cross-region latency, and a Devpost/judge-facing "Hackathon Experience" section |
| [glass-box-architecture.md](glass-box-architecture.md) | **Locked** architecture for Phase 6 / T7·T11·T16 (glass-box UI): why an insight's lineage is rendered but never cited (Q1), the trace carrying its own citable set (ADR-14.8), trace persistence at stage (G) and why it cannot sit inside the ingestion transaction, mechanical citation validation and its honest scope, and the M0–M8 plan |
| [consolidation-architecture.md](consolidation-architecture.md) | **Locked** architecture for Phase 5 / T5·T6·T12 (insight engine): why the designed analytics could not run on the data the replay committed, the two deterministic detectors that replace them, assertion-level series reading, insight identity by fingerprint + supersession, the post-commit (F₀) stage, derived freshness, and the M0–M7 plan |
| [replay-architecture.md](replay-architecture.md) | **Locked** architecture for Phase 4 / T8 (replay CLI): the dev-time/runtime split that makes replay zero-extraction, the idempotent resume ledger and why naive resume silently duplicates memories, the supersession-based correction workflow, period-expansion rules, the `normalize_item` contract, and the M1–M5 plan |
