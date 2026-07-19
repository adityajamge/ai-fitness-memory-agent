# AI Fitness Memory Agent

CockroachDB × AWS Agentic Memory Hackathon project (deadline 2026-08-19). An AI health companion whose core product is persistent, lifelong memory: a custom **Memory Engine** on CockroachDB (typed JSONB memories + `VECTOR(512)` embeddings, hybrid SQL+vector retrieval, deterministic EvidenceTraces driving a glass-box UI), a model-agnostic LangGraph agent (Bedrock default), FastAPI + Vite/React in one Docker image on Amazon ECS Express Mode (orig. App Runner; ADR-13.3 amendment). Standard multi-user SaaS — every account starts with empty memory.

**Source of truth: [docs/office-hours/README.md](docs/office-hours/README.md)** — read it (and its doc map) before designing, planning, or building anything. All architectural decisions live in [docs/office-hours/09-decisions.md](docs/office-hours/09-decisions.md) (ADR-1..13) — do not re-litigate them silently. **Day-to-day execution: [docs/implementation-roadmap.md](docs/implementation-roadmap.md)** (8 phases, demo checkpoints, commit milestones). Engineering backlog: [docs/office-hours/11-implementation-tasks.md](docs/office-hours/11-implementation-tasks.md) (T1–T18 details). Test obligations: [docs/office-hours/12-test-plan.md](docs/office-hours/12-test-plan.md). Deferred work: [TODOS.md](TODOS.md).

## Skill routing

When the user's request matches an available skill, invoke it via the Skill tool. When in doubt, invoke the skill.

Key routing rules:
- Product ideas/brainstorming → invoke /office-hours
- Strategy/scope → invoke /plan-ceo-review
- Architecture → invoke /plan-eng-review
- Design system/plan review → invoke /design-consultation or /plan-design-review
- Full review pipeline → invoke /autoplan
- Bugs/errors → invoke /investigate
- QA/testing site behavior → invoke /qa or /qa-only
- Code review/diff check → invoke /review
- Visual polish → invoke /design-review
- Ship/deploy/PR → invoke /ship or /land-and-deploy
- Save progress → invoke /context-save
- Resume context → invoke /context-restore
- Author a backlog-ready spec/issue → invoke /spec
