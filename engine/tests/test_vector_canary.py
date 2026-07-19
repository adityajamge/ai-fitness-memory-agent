"""Day-one canary #1 (T1): `VECTOR(512)` index + K-NN ordering on CockroachDB.

Proves the riskiest storage bets from ADR-13.2 / ADR-13.8 before any engine code
depends on them:

  1. A `VECTOR(512)` column and a C-SPANN `VECTOR INDEX` can be created on the
     target CockroachDB version/tier (the feature is preview-gated by a cluster
     setting on some versions).
  2. K-NN via the Euclidean operator `<->` returns exact expected ordering on
     *normalized* vectors — the design relies on unit vectors making L2 ordering
     equivalent to cosine ordering (Titan V2, 512-dim, normalized).
  3. The query plan actually uses the vector index (it must not be decorative).

This is a permanent CI test (single-node CockroachDB service container) and must
also be run once against the real CockroachDB Cloud cluster by pointing
DATABASE_URL at it — see docs/implementation-roadmap.md Phase 1.

Run locally:
    docker run -d --name crdb -p 26257:26257 cockroachdb/cockroach:latest \
        start-single-node --insecure
    # or a native binary: cockroach start-single-node --insecure --store=type=mem,size=1GiB
    pytest engine/tests/test_vector_canary.py

If no database is reachable the test SKIPS with a visible reason — unless CI or
REQUIRE_DB is set, in which case it FAILS (a canary must never silently skip in CI).
"""

import math
import os
import random
import uuid

import psycopg
import pytest

DIMS = 512
N_ROWS = 8
TOP_K = 5

# 127.0.0.1, not localhost: psycopg tries ::1 first and Windows takes ~130s to
# give up when the node listens on IPv4 only.
DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://root@127.0.0.1:26257/defaultdb?sslmode=disable"
)
_DB_REQUIRED = bool(os.environ.get("CI") or os.environ.get("REQUIRE_DB"))


def _unit(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec))
    return [x / norm for x in vec]


def _l2(a: list[float], b: list[float]) -> float:
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b, strict=True)))


def _vector_literal(vec: list[float]) -> str:
    return "[" + ",".join(f"{x:.9f}" for x in vec) + "]"


@pytest.fixture(scope="module")
def conn():
    try:
        connection = psycopg.connect(DATABASE_URL, autocommit=True, connect_timeout=5)
    except psycopg.OperationalError as exc:
        if _DB_REQUIRED:
            raise AssertionError(
                f"CI/REQUIRE_DB is set but CockroachDB is unreachable at {DATABASE_URL}: {exc}"
            ) from exc
        pytest.skip(f"no CockroachDB reachable at {DATABASE_URL} ({exc}); set REQUIRE_DB=1 to fail")
    yield connection
    connection.close()


def test_vector_512_index_and_knn_ordering(conn):
    table = f"vector_canary_{uuid.uuid4().hex[:8]}"

    with conn.cursor() as cur:
        try:
            cur.execute("SET CLUSTER SETTING feature.vector_index.enabled = true")
        except psycopg.Error:
            pass  # setting absent on versions where vector indexes are on by default

        rng = random.Random(42)
        rows = [
            (str(uuid.uuid4()), _unit([rng.uniform(-1.0, 1.0) for _ in range(DIMS)]))
            for _ in range(N_ROWS)
        ]
        # Query vector: a small perturbation of row 3, re-normalized, so the
        # expected nearest neighbor is unambiguous and ties are impossible.
        query = _unit([x + rng.uniform(-0.05, 0.05) for x in rows[3][1]])
        expected = [rid for rid, v in sorted(rows, key=lambda r: _l2(r[1], query))][:TOP_K]

        cur.execute(
            f"""
            CREATE TABLE {table} (
                id UUID PRIMARY KEY,
                embedding VECTOR({DIMS}),
                VECTOR INDEX embedding_idx (embedding)
            )
            """
        )
        try:
            # Row-at-a-time on purpose: C-SPANN degrades on large batch inserts
            # (the same reason replay uses small batches — see T8).
            for rid, vec in rows:
                cur.execute(
                    f"INSERT INTO {table} (id, embedding) VALUES (%s, %s::VECTOR({DIMS}))",
                    (rid, _vector_literal(vec)),
                )

            knn_sql = (
                f"SELECT id FROM {table} "
                f"ORDER BY embedding <-> %s::VECTOR({DIMS}) LIMIT {TOP_K}"
            )
            cur.execute(knn_sql, (_vector_literal(query),))
            got = [str(row[0]) for row in cur.fetchall()]
            assert got == expected, f"K-NN ordering mismatch:\n got {got}\n exp {expected}"

            cur.execute("EXPLAIN " + knn_sql, (_vector_literal(query),))
            plan = "\n".join(str(row[0]) for row in cur.fetchall())
            assert "vector search" in plan.lower() or "embedding_idx" in plan, (
                f"query plan does not use the vector index:\n{plan}"
            )
        finally:
            cur.execute(f"DROP TABLE IF EXISTS {table}")
