# 10 — Open Questions

> Part of the [office-hours canonical docs](README.md). These are deliberately **not**
> decided yet. Each names where it gets decided. When one is resolved, record the decision
> in [09-decisions.md](09-decisions.md) and check it off here.

| # | Question | Decide at | Notes |
|---|---|---|---|
| OQ1 | Embedding model (Bedrock Titan vs. alternative) + dimensionality | /plan-eng-review | Drives `VECTOR(n)` dims, index config, cost |
| OQ2 | Hosting target for the web app (ECS vs. Lambda+API GW vs. simplest viable) | /plan-eng-review | Whatever is chosen **deploys in Milestone 1** (ADR-11) |
| OQ3 | Judge sandbox isolation mechanics | /plan-eng-review | **Decided:** judges get a write-capable sandbox. Open: per-judge user vs. shared sandbox with reset; protecting the pristine demo user |
| OQ4 | Consolidation analytics scope (which changepoint / lagged-correlation methods) | /plan-eng-review + Milestone 2 | Bar: honest and simple, not a stats project |
| OQ5 | Does the real data yield the body-fat causal story, or a different one? | **The Assignment, before any code** | Go/no-go for the demo script; must survive sanitization (ADR-7) |
| OQ6 | Blood-report parsing depth in 40 days | Milestone 2 | Fallback: structured manual entry + one parsed example |
| OQ7 | Retraction mechanics (who evaluates conditions: on-ingest vs. on-read; supersession chaining) | /plan-eng-review | Status model itself is decided (ADR-9) |

## Advisory items carried from spec review (non-blocking)

- Milestone 1's public URL requires the cost guards listed in its checklist — treat as a
  deploy precondition, not an afterthought.
- The hosted demo DB serves the **sanitized derivative**, not the raw reconstruction — the
  judge sandbox is the highest-exposure surface.
