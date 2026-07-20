"""Auth routes: signup / login / logout (T9)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, field_validator

from api.auth import (
    AuthError,
    authenticate,
    create_session,
    create_user,
    delete_session,
)
from api.deps import SESSION_COOKIE
from engine.config import Settings
from engine.db import Database

router = APIRouter(prefix="/api/auth", tags=["auth"])


class Credentials(BaseModel):
    email: str
    password: str

    @field_validator("email")
    @classmethod
    def _valid_email(cls, v: str) -> str:
        if "@" not in v or "." not in v.split("@")[-1]:
            raise ValueError("invalid email address")
        return v.strip().lower()

    @field_validator("password")
    @classmethod
    def _min_length(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("password must be at least 8 characters")
        return v


def _set_session_cookie(response: Response, token: str, settings: Settings) -> None:
    # HttpOnly so JS can't read it; Lax is enough for a same-origin SPA. `secure` is left
    # off for local/dev over http — set SESSION_COOKIE_SECURE in production behind HTTPS.
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        httponly=True,
        samesite="lax",
        max_age=settings.session_ttl_seconds,
        path="/",
    )


@router.post("/signup")
def signup(creds: Credentials, request: Request, response: Response) -> dict:
    db: Database = request.app.state.db
    settings: Settings = request.app.state.settings
    with db.transaction() as cur:
        try:
            user_id = create_user(cur, creds.email, creds.password)
        except AuthError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        token, _ = create_session(cur, user_id, settings.session_ttl_seconds)
    _set_session_cookie(response, token, settings)
    return {"user_id": str(user_id), "email": creds.email}


@router.post("/login")
def login(creds: Credentials, request: Request, response: Response) -> dict:
    db: Database = request.app.state.db
    settings: Settings = request.app.state.settings
    with db.transaction() as cur:
        try:
            user_id = authenticate(cur, creds.email, creds.password)
        except AuthError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        token, _ = create_session(cur, user_id, settings.session_ttl_seconds)
    _set_session_cookie(response, token, settings)
    return {"user_id": str(user_id), "email": creds.email}


@router.post("/logout")
def logout(request: Request, response: Response) -> dict:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        db: Database = request.app.state.db
        with db.transaction() as cur:
            delete_session(cur, token)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}
