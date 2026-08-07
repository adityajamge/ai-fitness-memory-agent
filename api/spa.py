"""Serve the built Vite SPA from the same container as the API (ADR-13.3 / ADR-13.7).

One image, one origin: the browser fetches ``/`` and ``/api/*`` from the same host, so there is
no CORS layer, no API base URL to configure, and session cookies work without `SameSite`
gymnastics.

**The mount is optional by design.** ``web/dist`` does not exist during the Python test suite, in
a fresh clone, or when running `uvicorn` alongside `vite dev` — and none of those should break.
When the bundle is absent the app keeps its placeholder landing page and every API route behaves
exactly as before, so the deploy-early health check (T10) never depends on a frontend build.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

logger = logging.getLogger(__name__)

#: Paths the SPA fallback must never answer for. Without this guard, a typo'd API call would be
#: handed `index.html` with a 200, and the frontend would try to `JSON.parse` a page of HTML —
#: an error that looks like a parsing bug three layers away from its actual cause.
_RESERVED_PREFIXES = ("api/", "healthz", "docs", "redoc", "openapi.json")

#: Vite emits content-hashed filenames under ``assets/``, so those are immutable and cached hard.
#: ``index.html`` is the opposite: it names the current hashes and must never be cached, or a
#: deploy leaves browsers pinned to a bundle that no longer exists.
_IMMUTABLE = "public, max-age=31536000, immutable"
_NO_STORE = "no-store, must-revalidate"


class _HashedAssets(StaticFiles):
    """StaticFiles that marks content-hashed bundles immutable."""

    def file_response(self, *args, **kwargs) -> FileResponse:  # type: ignore[override]
        response = super().file_response(*args, **kwargs)
        response.headers["cache-control"] = _IMMUTABLE
        return response


def dist_dir() -> Path | None:
    """Locate the built SPA, or ``None`` when it has not been built.

    Resolved from the working directory (``/app`` in the container, the repo root in dev) rather
    than from this module's location: the package is pip-installed non-editable, so
    ``__file__`` points into site-packages while the bundle sits next to the source.
    """
    candidate = Path(os.environ.get("WEB_DIST", "web/dist"))
    return candidate if (candidate / "index.html").is_file() else None


def mount_spa(app: FastAPI) -> bool:
    """Mount the SPA if it was built. Returns whether it was.

    Must be called **after** every API router is registered. The catch-all matches any path
    FastAPI has not already claimed, so registering it first would shadow the whole API.
    """
    dist = dist_dir()
    if dist is None:
        logger.info("SPA bundle not found; serving the placeholder landing page")
        return False

    assets = dist / "assets"
    if assets.is_dir():
        app.mount("/assets", _HashedAssets(directory=assets), name="assets")

    index = dist / "index.html"

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa_fallback(full_path: str) -> FileResponse:
        """Client-side routing: every unclaimed path returns the shell.

        A deep link like ``/app`` or a refresh on ``/login`` reaches the server, not the router,
        so the shell has to answer and let React resolve the route.
        """
        if full_path.startswith(_RESERVED_PREFIXES):
            raise HTTPException(status_code=404, detail="not found")

        # Real files (favicon, robots.txt, og images) are served as themselves. `resolve()` plus
        # the parent check keeps `../` traversal out of the container filesystem.
        candidate = (dist / full_path).resolve()
        if full_path and candidate.is_file() and dist.resolve() in candidate.parents:
            return FileResponse(candidate)

        return FileResponse(index, headers={"cache-control": _NO_STORE})

    logger.info("SPA mounted from %s", dist)
    return True
