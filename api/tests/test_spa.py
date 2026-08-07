"""SPA mount contract (F3).

These pin the one thing that could silently break the whole API: a catch-all route registered to
serve `index.html` for client-side routing will, if it is even slightly too greedy, answer for
`/api/*` too — and the frontend then tries to `JSON.parse` a page of HTML, producing an error
three layers away from its cause.

Built with a synthetic ``dist`` rather than the real one so the suite behaves identically whether
or not the developer has run ``npm run build``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.spa import dist_dir, mount_spa

SHELL = "<!doctype html><html><body><div id='root'></div></body></html>"


@pytest.fixture
def dist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A minimal built-SPA layout: a shell, a hashed asset, and a static file."""
    root = tmp_path / "dist"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text(SHELL, encoding="utf-8")
    (root / "assets" / "index-abc123.js").write_text("console.log(1)", encoding="utf-8")
    (root / "robots.txt").write_text("User-agent: *", encoding="utf-8")
    monkeypatch.setenv("WEB_DIST", str(root))
    return root


@pytest.fixture
def client(dist: Path) -> TestClient:
    app = FastAPI()

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/stats")
    def stats() -> dict[str, int]:
        return {"memories": 0}

    assert mount_spa(app) is True
    return TestClient(app)


def test_absent_bundle_is_not_an_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A fresh clone, the Python test suite, and `vite dev` all lack a bundle. None may break —
    the deploy-early health check (T10) must never depend on a frontend build."""
    monkeypatch.setenv("WEB_DIST", str(tmp_path / "nothing-here"))
    assert dist_dir() is None
    assert mount_spa(FastAPI()) is False


def test_root_serves_the_shell(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "<div id='root'>" in response.text


def test_deep_link_serves_the_shell(client: TestClient) -> None:
    """A refresh on /app or /login reaches the server, not the router. The shell has to answer
    or client-side routing dies on every reload."""
    for path in ("/app", "/login", "/signup", "/some/nested/route"):
        response = client.get(path)
        assert response.status_code == 200, path
        assert "<div id='root'>" in response.text, path


@pytest.mark.parametrize("path", ["/api/nope", "/api/turns/x/trace", "/api/"])
def test_unknown_api_paths_404_rather_than_returning_the_shell(
    client: TestClient, path: str
) -> None:
    """404 as JSON, not 200 as HTML. This is the regression that would look like a frontend
    parsing bug for a day before anyone suspected routing."""
    response = client.get(path)
    assert response.status_code == 404
    assert "<div id='root'>" not in response.text


@pytest.mark.parametrize("path", ["/docs", "/openapi.json"])
def test_framework_routes_keep_their_own_content(client: TestClient, path: str) -> None:
    """FastAPI's own routes are registered before the catch-all, so they answer normally. The
    contract here is not "404" — it is "never the SPA shell"."""
    response = client.get(path)
    assert response.status_code == 200
    assert "<div id='root'>" not in response.text


def test_registered_routes_still_win(client: TestClient) -> None:
    """The catch-all matches only what FastAPI has not already claimed."""
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/api/stats").json() == {"memories": 0}


def test_real_files_are_served_as_themselves(client: TestClient) -> None:
    assert client.get("/robots.txt").text == "User-agent: *"


def test_hashed_assets_are_immutable_and_the_shell_is_not(client: TestClient) -> None:
    """Hashed bundles cache forever; index.html names those hashes and must never be cached, or
    a deploy pins browsers to a bundle that no longer exists."""
    assert "immutable" in client.get("/assets/index-abc123.js").headers["cache-control"]
    assert client.get("/").headers["cache-control"] == "no-store, must-revalidate"


def test_path_traversal_does_not_escape_the_bundle(client: TestClient) -> None:
    response = client.get("/../../pyproject.toml")
    assert "[project]" not in response.text
