"""FastAPI app factory — Phase 2 write path plus the Phase 3 agent, behind auth, on the
Phase 1 deploy-early spine (T10, ADR-11).

``create_app`` is a factory so tests can inject a fake model provider; production calls it
with no arguments and gets the Bedrock provider. Startup applies the schema idempotently
(D4) and opens the LangGraph checkpointer (``.setup()`` once — ADR-13.14 footgun), then
compiles the turn graph. Both tolerate an unreachable database so the ECS health check stays
green; ``/api/chat`` reports 503 until the cluster is back.

The built Vite SPA is served from this same app when ``web/dist`` exists (``api/spa.py``), so one
container answers both the UI and the API on one origin. SSE arrives with the live engine pane.
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
from api.routers import glassbox as glassbox_router
from api.routers import ingest as ingest_router
from api.routers import profile as profile_router
from api.routers import today as today_router
from api.spa import mount_spa
from engine.config import Settings, load_settings
from engine.consolidation import ConsolidationService
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
        # ONE consolidation service, shared by both callers (Phase 5 §4.9): the ingestion
        # tail's stage (F₀) and the graph's analyze_series node. A second instance would be a
        # second place the identity rule lives, which is exactly what I-12 exists to prevent.
        consolidation = ConsolidationService(db, default_tz=settings.default_tz)
        ingestion = IngestionService(
            db,
            model,
            default_tz=settings.default_tz,
            backfill_batch=settings.backfill_batch,
            consolidation=consolidation,
        )
        app.state.settings = settings
        app.state.db = db
        app.state.ingestion = ingestion
        app.state.consolidation = consolidation

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
                consolidation=consolidation,
                history_max_turns=settings.history_max_turns,
                history_max_chars=settings.history_max_chars,
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
    app.include_router(glassbox_router.router)
    app.include_router(profile_router.router)
    app.include_router(today_router.router)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        """ALB health check target (ECS Express Mode)."""
        return {"status": "ok"}

    # The SPA claims "/" and every unrouted path, so it must be mounted *after* the routers
    # above or its catch-all would shadow the entire API. When `web/dist` is absent — the
    # Python test suite, a fresh clone, `vite dev` alongside uvicorn — this is a no-op and the
    # placeholder below answers instead.
    if not mount_spa(app):

        @app.get("/", response_class=HTMLResponse)
        def root() -> str:
            return _HELLO

    return app


app = create_app()
