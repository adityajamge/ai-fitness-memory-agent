"""FastAPI app factory — Phase 2 write path plus the Phase 3 agent, behind auth, on the
Phase 1 deploy-early spine (T10, ADR-11).

``create_app`` is a factory so tests can inject a fake model provider; production calls it
with no arguments and gets the Bedrock provider. Startup applies the schema idempotently
(D4) and opens the LangGraph checkpointer (``.setup()`` once — ADR-13.14 footgun), then
compiles the turn graph. Both tolerate an unreachable database so the ECS health check stays
green; ``/api/chat`` reports 503 until the cluster is back. SSE and the SPA arrive in Phase 6.
"""

from __future__ import annotations

import logging
from contextlib import ExitStack, asynccontextmanager

import psycopg
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from agent.checkpointer import CockroachDBSaver
from agent.graph import build_graph
from agent.providers import build_default_provider
from api.routers import auth as auth_router
from api.routers import chat as chat_router
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
  <p><strong>Status: Phase 3</strong> &mdash; the memory write path and the agent are live
     (<code>POST /api/auth/signup</code>, <code>POST /api/ingest</code>,
     <code>POST /api/chat</code>): ask a question and the agent answers from memory, with
     memory-ID citations and the evidence trace that produced them. The glass-box UI lands
     next.</p>
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
        ingestion = IngestionService(
            db, model, default_tz=settings.default_tz, backfill_batch=settings.backfill_batch
        )
        app.state.settings = settings
        app.state.db = db
        app.state.ingestion = ingestion

        # The checkpointer holds one long-lived connection for the app's lifetime and runs
        # its migrations once (ADR-13.14). Same tolerance as the schema apply: without it
        # the graph is simply absent and /api/chat answers 503.
        resources = ExitStack()
        app.state.graph = None
        try:
            saver = resources.enter_context(
                CockroachDBSaver.from_conn_string(settings.database_url)
            )
            saver.setup()
            app.state.graph = build_graph(
                db=db,
                model=model,
                ingestion=ingestion,
                checkpointer=saver,
                default_tz=settings.default_tz,
            )
        except psycopg.OperationalError:
            logger.warning(
                "agent graph unavailable: database unreachable at startup", exc_info=True
            )

        try:
            yield
        finally:
            resources.close()

    app = FastAPI(title="AI Fitness Memory Agent", version="0.3.0", lifespan=lifespan)
    app.include_router(auth_router.router)
    app.include_router(ingest_router.router)
    app.include_router(chat_router.router)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        """ALB health check target (ECS Express Mode)."""
        return {"status": "ok"}

    @app.get("/", response_class=HTMLResponse)
    def root() -> str:
        return _HELLO

    return app


app = create_app()
