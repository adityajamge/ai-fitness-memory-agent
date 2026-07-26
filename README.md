# AI Fitness Memory Agent

> An AI health companion that never forgets — persistent, lifelong memory as the product,
> built on CockroachDB and AWS. Entry for the **CockroachDB × AWS Agentic Memory Hackathon**.

Most AI assistants treat every conversation independently. This agent turns every meal photo,
workout, body scan, blood report, and conversation into **typed, evidence-grade memories** in
CockroachDB — then reasons across months of history: *"What changed before my body fat
started dropping?"* gets a dated, memory-ID-cited answer, with a **glass-box UI** showing the
raw evidence rows and the actual SQL + vector queries that produced it.

**Status: Phase 2 — memory write path complete ✅ (2026-07-20).** Signing up and logging a
meal creates typed, embedded, never-lost memories in CockroachDB (`POST /api/auth/signup`,
`POST /api/ingest`), behind per-user scoping that is tested as a security boundary. Phase 1
before it: day-one canaries (vector index, checkpointer) green against CockroachDB Cloud and
CI/CD live end-to-end — every push to `main` tests against real CockroachDB, builds the image,
and deploys to ECS Express Mode ([docs/deploy.md](docs/deploy.md)). Retrieval and the agent
land in Phase 3; the glass-box UI in Phase 6. Built solo with Claude Code; deadline
2026-08-19. Execution plan: [docs/implementation-roadmap.md](docs/implementation-roadmap.md).

## Live deployment

- **Application:** https://ai-2e921ede8718444985c5b24e7fb23497.ecs.us-east-1.on.aws
  (status page — the product lands here phase by phase; the Phase-2 write-path routes need
  the runtime configuration in [docs/deploy.md → Runtime configuration](docs/deploy.md#runtime-configuration-phase-2-onward))
- **Health check:** [/healthz](https://ai-2e921ede8718444985c5b24e7fb23497.ecs.us-east-1.on.aws/healthz)
- **API docs (FastAPI):** [/docs](https://ai-2e921ede8718444985c5b24e7fb23497.ecs.us-east-1.on.aws/docs)

## Architecture in one paragraph

A custom **Memory Engine** (deterministic; the centerpiece) owns ingestion, hybrid SQL +
vector retrieval, event-driven consolidation into derived insights with lineage and
retraction, and construction of `EvidenceTrace` artifacts that drive the UI. A model-agnostic
**LangGraph agent** (Amazon Bedrock by default) is the only natural-language layer — it emits
typed tool calls; the LLM narrates but never generates SQL and never feeds the glass box.
Storage is **CockroachDB** (typed JSONB payloads + `VECTOR(512)` embeddings in one
transactionally consistent store); hosting is a single Docker image on **Amazon ECS
Express Mode** (Fargate + ALB);
photos/reports live in **S3**. Standard multi-user SaaS — every account starts with empty
memory. Full design: [docs/office-hours/](docs/office-hours/README.md).

## Repository structure

```
engine/     Memory Engine package (deterministic core)     → Phase 2+
agent/      LangGraph agent: planner, tools, narration     → Phase 3
api/        FastAPI app: auth, turns, traces, SSE, SPA     → Phase 2+
web/        Vite + React glass-box UI                      → Phase 6
cli/        migrate + embedding backfill → Phase 2; replay → Phase 4
evals/      Live-model eval suites                         → Phase 7
docs/       Canonical design docs (source of truth)
  office-hours/            Architecture, ADRs, task backlog, test plan
  implementation-roadmap.md  Day-to-day execution phases
```

Each package carries a docstring pointing at its design doc. `docs/office-hours/09-decisions.md`
(ADR-1..13) records every architectural decision and its rejected alternatives — read before
re-opening anything.

## Development

```bash
pip install -e . --group dev        # editable install + pytest/ruff (needs pip ≥ 25.1)
cp .env.example .env                # then edit — see Configuration below
pytest                              # canaries skip visibly without a DB; REQUIRE_DB=1 to enforce
uvicorn api.main:app --port 8080    # run the app locally
```

Python ≥ 3.10. Runtime dependencies are added phase-by-phase (see `pyproject.toml`).
Tests run against a real single-node CockroachDB — start one with
`docker run -d -p 26257:26257 cockroachdb/cockroach:v26.2.4 start-single-node --insecure`
(or a native binary; see canary docstrings). CI does the same on every push and also
builds + smoke-tests the Docker image. Deployment: [docs/deploy.md](docs/deploy.md).

## Configuration

All configuration is environment variables, read in [`engine/config.py`](engine/config.py).
Copy [`.env.example`](.env.example) to `.env` — it documents every variable, which are
required, and what each one defaults to.

In development the `.env` file is loaded automatically (by `load_settings()`, and by the root
`conftest.py` so tests see the same configuration the app does). **Real environment variables
always win**, so `DATABASE_URL=... pytest` still overrides the file. In the deployed image
there is no `.env` at all — `python-dotenv` is a dev-only dependency, and ECS supplies real
variables ([docs/deploy.md → Runtime configuration](docs/deploy.md#runtime-configuration-phase-2-onward)).

Only one variable has no usable default in practice:

| Variable | Required? | Default |
|---|---|---|
| `DATABASE_URL` | in practice yes | a local single-node CockroachDB URL |
| everything else | no | see [`.env.example`](.env.example) |

Model access is not configured by environment variables: Amazon Bedrock credentials come
from the AWS credential chain (locally `aws configure`/SSO, in ECS the task role). Only the
model *ids* and region are overridable.

## Hackathon compliance (evidence lands in later phases)

- **CockroachDB tools:** Distributed Vector Indexing (runtime), Managed MCP Server
  (AI-assisted development), ccloud CLI (cluster provisioning — recorded)
- **AWS services:** Amazon Bedrock (LLM, vision, Titan embeddings), Amazon S3, Amazon ECS
  (Express Mode)

## License

[MIT](LICENSE) © 2026 Aditya Babanrao Jamge
