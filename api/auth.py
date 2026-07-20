"""Email+password auth + opaque sessions (T9, ADR-13.15 — simple by design).

Passwords are hashed with stdlib ``hashlib.scrypt`` (D2 — no external auth dependency),
per-user random salt. Sessions are random opaque tokens in the ``sessions`` table, delivered
as an HttpOnly cookie by the router. Production abuse/spend controls are explicitly out of
scope this iteration (ADR-13.15, TODOS.md).
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID

import psycopg

# scrypt cost parameters: ~16 MiB, well within a 64 MiB ceiling. Interactive-login speed.
_SCRYPT = {"n": 2**14, "r": 8, "p": 1}
_DKLEN = 32
_MAXMEM = 64 * 1024 * 1024
_SALT_BYTES = 16
_TOKEN_BYTES = 32


class AuthError(Exception):
    """Signup/login failure (duplicate email, bad credentials). Mapped to 4xx by the router."""


def hash_password(password: str, salt: bytes | None = None) -> tuple[bytes, bytes]:
    salt = salt or os.urandom(_SALT_BYTES)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, dklen=_DKLEN, maxmem=_MAXMEM, **_SCRYPT
    )
    return digest, salt


def verify_password(password: str, salt: bytes, expected: bytes) -> bool:
    digest, _ = hash_password(password, salt)
    return hmac.compare_digest(digest, expected)


def create_user(cur: psycopg.Cursor, email: str, password: str) -> UUID:
    digest, salt = hash_password(password)
    try:
        cur.execute(
            "INSERT INTO users (email, password_hash, salt) VALUES (%s, %s, %s) RETURNING id",
            [email, digest, salt],
        )
    except psycopg.errors.UniqueViolation as exc:
        raise AuthError("email already registered") from exc
    return cur.fetchone()["id"]


def authenticate(cur: psycopg.Cursor, email: str, password: str) -> UUID:
    cur.execute("SELECT id, password_hash, salt FROM users WHERE email = %s", [email])
    row = cur.fetchone()
    # Verify even on a miss would be ideal to equalize timing; kept simple per ADR-13.15.
    if row is None or not verify_password(
        password, bytes(row["salt"]), bytes(row["password_hash"])
    ):
        raise AuthError("invalid email or password")
    return row["id"]


def create_session(cur: psycopg.Cursor, user_id: UUID, ttl_seconds: int) -> tuple[str, datetime]:
    token = secrets.token_urlsafe(_TOKEN_BYTES)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
    cur.execute(
        "INSERT INTO sessions (token, user_id, expires_at) VALUES (%s, %s, %s)",
        [token, user_id, expires_at],
    )
    return token, expires_at


def resolve_session(cur: psycopg.Cursor, token: str) -> UUID | None:
    cur.execute("SELECT user_id, expires_at FROM sessions WHERE token = %s", [token])
    row = cur.fetchone()
    if row is None or row["expires_at"] <= datetime.now(timezone.utc):
        return None
    return row["user_id"]


def delete_session(cur: psycopg.Cursor, token: str) -> None:
    cur.execute("DELETE FROM sessions WHERE token = %s", [token])
