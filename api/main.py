"""FastAPI app entrypoint — Phase 1 deploy-early placeholder (T10, ADR-11).

Serves a hello page and a health endpoint so the deploy pipeline (ECS Express
Mode) exists before any feature code does; every later phase improves this
live app.
Auth, turns, traces, SSE, and SPA serving arrive in Phase 2+
(docs/office-hours/02-architecture-overview.md).
"""

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

app = FastAPI(title="AI Fitness Memory Agent", version="0.1.0")

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
  <p><strong>Status: Phase 1</strong> &mdash; deploy-early placeholder. The Memory Engine,
     agent, and glass-box UI land here phase by phase.</p>
  <p><a href="https://github.com/adityajamge/ai-fitness-memory-agent">Source &amp; design
     docs on GitHub</a></p>
</body>
</html>
"""


@app.get("/healthz")
def healthz() -> dict[str, str]:
    """ALB health check target (ECS Express Mode)."""
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def root() -> str:
    return _HELLO
