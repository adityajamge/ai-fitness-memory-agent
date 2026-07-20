"""FastAPI dependencies: shared app state + the auth boundary.

``get_current_user`` is the single place a request's identity is established — always from the
session cookie, **never** from the request body. Every downstream engine query is scoped to
this user_id, which is what makes cross-user access impossible (ADR-13.4; tested by
api/tests/test_scoping.py).
"""

from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException, Request

from api.auth import resolve_session
from engine.config import Settings
from engine.db import Database
from engine.ingestion import IngestionService

SESSION_COOKIE = "session"


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_db(request: Request) -> Database:
    return request.app.state.db


def get_ingestion(request: Request) -> IngestionService:
    return request.app.state.ingestion


def get_current_user(request: Request) -> UUID:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(status_code=401, detail="not authenticated")
    db: Database = request.app.state.db
    with db.transaction() as cur:
        user_id = resolve_session(cur, token)
    if user_id is None:
        raise HTTPException(status_code=401, detail="session invalid or expired")
    return user_id
