# AI Fitness Memory Agent

> An AI health companion that never forgets — persistent, lifelong memory as the product,
> built on CockroachDB and AWS. Entry for the **CockroachDB × AWS Agentic Memory Hackathon**.

Most AI assistants treat every conversation independently. This agent turns every meal photo,
workout, body scan, blood report, and conversation into **typed, evidence-grade memories** in
CockroachDB — then reasons across months of history: *"What changed before my body fat
started dropping?"* gets a dated, memory-ID-cited answer, with a **glass-box UI** showing the
raw evidence rows and the actual SQL + vector queries that produced it.

**Status: Phase 1 (cloud foundations).** Day-one canaries (vector index, checkpointer)
green against CockroachDB Cloud; CI + ECS Express Mode deploy pipeline in place
([docs/deploy.md](docs/deploy.md)). Built solo with Claude Code; deadline 2026-08-19.
Execution plan: [docs/implementation-roadmap.md](docs/implementation-roadmap.md).

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
cli/        Replay (seed reconstruction) + backfill tools  → Phase 4
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
pytest                              # canaries skip visibly without a DB; REQUIRE_DB=1 to enforce
uvicorn api.main:app --port 8080    # run the app locally
```

Python ≥ 3.10. Runtime dependencies are added phase-by-phase (see `pyproject.toml`).
Tests run against a real single-node CockroachDB — start one with
`docker run -d -p 26257:26257 cockroachdb/cockroach:v26.2.4 start-single-node --insecure`
(or a native binary; see canary docstrings). CI does the same on every push and also
builds + smoke-tests the Docker image. Deployment: [docs/deploy.md](docs/deploy.md).

## Hackathon compliance (evidence lands in later phases)

- **CockroachDB tools:** Distributed Vector Indexing (runtime), Managed MCP Server
  (AI-assisted development), ccloud CLI (cluster provisioning — recorded)
- **AWS services:** Amazon Bedrock (LLM, vision, Titan embeddings), Amazon S3, Amazon ECS
  (Express Mode)

## License

[MIT](LICENSE) © 2026 Aditya Babanrao Jamge
