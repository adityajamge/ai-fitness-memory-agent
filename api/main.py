"""FastAPI app factory — Phase 2 write path behind auth (T4/T9/T15), on the Phase 1
deploy-early spine (T10, ADR-11).

``create_app`` is a factory so tests can inject a fake model provider; production calls it
with no arguments and gets the Bedrock provider. Schema is applied idempotently on startup
(D4) alongside the LangGraph checkpointer's own ``.setup()``. Retrieval, the agent graph,
SSE, and the SPA arrive in Phase 3+.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import psycopg
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from agent.providers import build_default_provider
from api.routers import auth as auth_router
from api.routers import ingest as ingest_router
from engine.config import Settings, load_settings
from engine.db import Database
from engine.ingestion import IngestionService
from engine.model import ModelProvider

logger = logging.getLogger(__name__)

_HELLO = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AI Fitness Memory Agent</title>
  <style>
    body { font-family: system-ui, sans-serif; max-width: 42rem; margin: 4rem auto;
           padding: 0 1rem; line-height: 1.6; }
    code { background: #f0f0f0; padding: 0.1em 0.3em; border-radius: 3px; }
  </style>
</head>
<body>
  <h1>AI Fitness Memory Agent</h1>
  <p>An AI health companion that never forgets &mdash; persistent, lifelong memory on
     CockroachDB and AWS. Entry for the CockroachDB &times; AWS Agentic Memory Hackathon.</p>
  <p><strong>Status: Phase 2</strong> &mdash; the memory write path is live (<code>POST
     /api/auth/signup</code>, <code>POST /api/ingest</code>). Retrieval, the agent, and the
     glass-box UI land here phase by phase.</p>
  <p><a href="https://github.com/adityajamge/ai-fitness-memory-agent">Source &amp; design
     docs on GitHub</a></p>
</body>
</html>
"""


def create_app(
    *, settings: Settings | None = None, provider: ModelProvider | None = None
) -> FastAPI:
    settings = settings or load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db = Database(settings.database_url)
        # Idempotent schema apply (D4). Tolerate an unreachable DB at boot so the
        # deploy-early health check (ECS Express) stays green and the container doesn't
        # crash-loop; DB-backed routes surface errors until the cluster is reachable.
        try:
            db.setup_schema()
        except psycopg.OperationalError:
            logger.warning("schema setup skipped: database unreachable at startup", exc_info=True)
        model = provider or build_default_provider(settings)
        app.state.settings = settings
        app.state.db = db
        app.state.ingestion = IngestionService(
            db, model, default_tz=settings.default_tz, backfill_batch=settings.backfill_batch
        )
        yield

    app = FastAPI(title="AI Fitness Memory Agent", version="0.2.0", lifespan=lifespan)
    app.include_router(auth_router.router)
    app.include_router(ingest_router.router)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        """ALB health check target (ECS Express Mode)."""
        return {"status": "ok"}

    @app.get("/", response_class=HTMLResponse)
    def root() -> str:
        return _HELLO

    return app


app = create_app()
