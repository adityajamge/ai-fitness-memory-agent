# AI Fitness Memory Agent

CockroachDB × AWS Agentic Memory Hackathon project (deadline 2026-08-19). An AI health companion whose core product is persistent, lifelong memory: a custom **Memory Engine** on CockroachDB (typed JSONB memories + `VECTOR(512)` embeddings, hybrid SQL+vector retrieval, deterministic EvidenceTraces driving a glass-box UI), a model-agnostic LangGraph agent (Bedrock default), FastAPI + Vite/React in one Docker image on Amazon ECS Express Mode (orig. App Runner; ADR-13.3 amendment). Standard multi-user SaaS — every account starts with empty memory.

**Source of truth: [docs/office-hours/README.md](docs/office-hours/README.md)** — read it (and its doc map) before designing, planning, or building anything. All architectural decisions live in [docs/office-hours/09-decisions.md](docs/office-hours/09-decisions.md) (ADR-1..13) — do not re-litigate them silently. **Day-to-day execution: [docs/implementation-roadmap.md](docs/implementation-roadmap.md)** (8 phases, demo checkpoints, commit milestones). Engineering backlog: [docs/office-hours/11-implementation-tasks.md](docs/office-hours/11-implementation-tasks.md) (T1–T18 details). Test obligations: [docs/office-hours/12-test-plan.md](docs/office-hours/12-test-plan.md). Deferred work: [TODOS.md](TODOS.md).

## Design System

**Always read [DESIGN.md](DESIGN.md) before making any visual or UI decision.** It is the locked
visual contract for Phase 6 (M4–M8): typography, color tokens, spacing, radius, elevation, motion,
responsive behavior, accessibility standards, the component language, the landing-page spec, and
the twenty design rules every component must obey. Font choices, colors, spacing, and aesthetic
direction are defined there and nowhere else.

Do not deviate without explicit user approval plus a Decisions Log entry in DESIGN.md. In review
and QA, flag any code that does not match it. The three rules most often broken: mono is reserved
for database-originated values (never for style), no hardcoded colors (tokens only), and every
data-driven component ships empty, loading, and error states alongside the populated one.

**Also read [docs/engineering/frontend-guidelines.md](docs/engineering/frontend-guidelines.md)
before writing any code in `web/`** — it is the engineering contract (layout, the single fetch
boundary, state, motion under `LazyMotion strict`, accessibility, the agent toolkit, the review
checklist). DESIGN.md wins on appearance; that file wins on structure.

Frontend status, the foundation commit, and what to build first are in
**[DESIGN.md §0](DESIGN.md#0-frontend-foundation-status)** and
**[§15 M4 build order](DESIGN.md#15-m4-build-order)**.

**Product surfaces (as of 2026-08-13):** three, and three is a stated ceiling —
**Chat** (`/app`, the conversation and default/home surface, [§9](DESIGN.md)),
**Review** (`/app/review`, the memory briefing — renamed and rebuilt from Today by the
2026-08-13 IA revision, [DESIGN.md §6.20](DESIGN.md)), and **Profile**
(`/app/profile`, identity/goals/account, [§6.19](DESIGN.md)). A fourth needs a
Decisions Log entry. Chat and the original Today came out of the competitive research
approved 2026-08-12, whose finding was that an AI coach grounded in your own data is
now **table stakes** — four of the five products studied shipped one — while
resolving a claim to the rows that produced it is what nobody else does; the
2026-08-13 revision restructured the IA around that same finding (full rationale in
[DESIGN.md §16](DESIGN.md#16-decisions-log)), it did not reopen it. Note this
supersedes an earlier forward-looking note in this file about a *separate*,
not-yet-started "generated weekly review" surface — that idea, if still wanted, needs
a different name now that "Review" names the shipped memory-briefing screen instead.

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
