"""T12 — latency profile against the deployed cross-region path (Phase 5 M6).

**Why this exists and what it measures.** ADR-13.1's consolidation budget (~300ms) was
provisional; §11.3 of docs/engineering/consolidation-architecture.md records a measured
~635ms/series figure from ad-hoc Phase 5 M3 development, taken against the real cross-region
path (app in us-east-1, CockroachDB Cloud in ap-south-1). T12 exists to re-derive that number
properly: a local measurement answers a different question than the one the budget is about,
because the round trip *is* the thing being measured (TODOS.md, "M6 — Latency profile").

This script measures three turn types against a real, reachable app instance over real HTTP:
  - **ingest**: a turn that logs a meal (exercises the write path: extract, validate, embed,
    insert, opportunistic consolidation).
  - **query**: a turn that asks a question (exercises retrieval, assembly, narration).
  - **both**: a turn that logs and asks in the same message.

**Non-invasive by design** (consolidation-architecture.md §8/M6's invariant: "instrumentation
must not alter the path it measures"). This is a plain HTTP client timing full round trips
against the public API surface — nothing is added to the measured request path itself.

**"Cold" vs "warm" is an approximation, stated honestly.** This script cannot force a true
infrastructure cold start (that requires the ECS task to have been idle long enough to be
recycled, or a fresh deploy) — it can only mark the *first* request of a run as "cold" as
observed from the client. Treat the cold/warm split here as "first request vs steady state,"
not as a controlled cold-start benchmark.

**Safety rail.** Running this against a real deployed URL creates a real account and real
memory rows on that system — it is not a read-only operation. It refuses to run without
``--i-understand-this-mutates-production`` so it is never fired accidentally (e.g. copy-pasted
from a --help example). Point it at a local dev server with ``--out`` set somewhere other than
``docs/latency.md`` for a tooling shakedown; local numbers are not production numbers and must
never be written to that file (T12's own rule — see the module docstring above).

Usage:
    # Production run, once AWS access is restored and a verified deploy exists (see TODOS.md
    # "M6 — Latency profile (T12)" for the exact prerequisites):
    python -m cli.latency_probe --url https://<deployed-url> \\
        --samples 20 --i-understand-this-mutates-production

    # Local tooling shakedown (never writes docs/latency.md):
    python -m cli.latency_probe --url http://127.0.0.1:8091 --samples 3 \\
        --out scratch/latency-local-shakedown.md --i-understand-this-mutates-production
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import statistics
import sys
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

_TURN_TYPES = ("ingest", "query", "both")


@dataclass
class Sample:
    turn_type: str
    is_cold: bool
    elapsed_ms: float
    status: int


def _opener() -> urllib.request.OpenerDirector:
    """A cookie-jar-backed opener so the session cookie from signup rides every later
    request, matching the app's same-origin cookie auth (frontend-guidelines.md §2)."""
    jar = http.cookiejar.CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def _post(
    opener: urllib.request.OpenerDirector, base_url: str, path: str, payload: dict
) -> tuple[int, float]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        base_url.rstrip("/") + path,
        data=data,
        headers={"content-type": "application/json"},
        method="POST",
    )
    start = time.monotonic()
    try:
        with opener.open(req, timeout=60) as resp:
            resp.read()
            status = resp.status
    except urllib.error.HTTPError as exc:
        exc.read()
        status = exc.code
    return status, (time.monotonic() - start) * 1000


def _signup(opener: urllib.request.OpenerDirector, base_url: str) -> str:
    """A fresh throwaway account per run — never reuses a real one. Falls back to login in
    the (unlikely) case the generated email already exists."""
    email = f"latency-probe-{uuid.uuid4().hex}@example.com"
    password = uuid.uuid4().hex
    creds = {"email": email, "password": password}
    status, _ = _post(opener, base_url, "/api/auth/signup", creds)
    if status not in (200, 201):
        status, _ = _post(opener, base_url, "/api/auth/login", creds)
        if status != 200:
            raise SystemExit(f"could not authenticate against {base_url} (status {status})")
    return email


def _measure(
    opener: urllib.request.OpenerDirector,
    base_url: str,
    turn_type: str,
    message: str,
    thread_id: str,
    is_cold: bool,
) -> Sample:
    status, elapsed_ms = _post(
        opener, base_url, "/api/chat", {"message": message, "thread_id": thread_id}
    )
    return Sample(turn_type=turn_type, is_cold=is_cold, elapsed_ms=elapsed_ms, status=status)


def run(base_url: str, samples: int) -> list[Sample]:
    opener = _opener()
    _signup(opener, base_url)
    thread_id = f"latency-probe-{uuid.uuid4().hex}"

    results: list[Sample] = []
    for i in range(samples):
        is_cold = i == 0
        now = datetime.now(timezone.utc).isoformat()
        results.append(
            _measure(
                opener, base_url, "ingest", f"logged {100 + i}g rice at {now}", thread_id, is_cold
            )
        )
        results.append(
            _measure(
                opener,
                base_url,
                "query",
                "how much rice have I logged today?",
                thread_id,
                is_cold,
            )
        )
        results.append(
            _measure(
                opener,
                base_url,
                "both",
                f"logged another {50 + i}g rice — how much have I had today?",
                thread_id,
                is_cold,
            )
        )
    return results


def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    k = (len(ordered) - 1) * (pct / 100)
    lo, hi = int(k), min(int(k) + 1, len(ordered) - 1)
    if lo == hi:
        return ordered[lo]
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)


def summarize(samples: list[Sample]) -> dict:
    summary: dict = {}
    for turn_type in _TURN_TYPES:
        this_type = [s for s in samples if s.turn_type == turn_type]
        warm = [s.elapsed_ms for s in this_type if not s.is_cold and s.status < 400]
        cold = [s.elapsed_ms for s in this_type if s.is_cold and s.status < 400]
        failed = [s for s in this_type if s.status >= 400]
        summary[turn_type] = {
            "n_warm": len(warm),
            "n_cold": len(cold),
            "n_failed": len(failed),
            "p50_ms": _percentile(warm, 50),
            "p95_ms": _percentile(warm, 95),
            "mean_ms": statistics.fmean(warm) if warm else None,
            "cold_ms": cold[0] if cold else None,
        }
    return summary


def write_report(
    path: str, base_url: str, samples: int, results: list[Sample], summary: dict
) -> None:
    lines = [
        "# Latency profile (T12)",
        "",
        f"- **Environment**: `{base_url}`",
        f"- **Measured at**: {datetime.now(timezone.utc).isoformat()}",
        f"- **Samples per turn type**: {samples}",
        "- **Methodology**: plain HTTP client timing of full `POST /api/chat` round trips "
        "(cli/latency_probe.py). Non-invasive — no instrumentation was added to the measured "
        "path. \"Cold\" is the first request of the run as observed from the client, not a "
        "controlled infrastructure cold start (see the script's module docstring).",
        "",
        "## Results",
        "",
        "| turn type | n (warm) | p50 (ms) | p95 (ms) | mean (ms) | first request (ms) | failed |",
        "|---|---|---|---|---|---|---|",
    ]
    for turn_type in _TURN_TYPES:
        s = summary[turn_type]

        def _fmt(v: float | None) -> str:
            return f"{v:.0f}" if v is not None else "—"

        lines.append(
            f"| {turn_type} | {s['n_warm']} | {_fmt(s['p50_ms'])} | {_fmt(s['p95_ms'])} | "
            f"{_fmt(s['mean_ms'])} | {_fmt(s['cold_ms'])} | {s['n_failed']} |"
        )
    lines += [
        "",
        "## ADR-13.1 verdict",
        "",
        "_Fill in by hand after a real production run_: does the ~300ms consolidation budget "
        "(consolidation-architecture.md §11.3's ~635ms/series figure) still hold, confirm or "
        "amend it, and answer open question Q3 (does the deferral path need a catch-up "
        "trigger?).",
        "",
        "## Raw samples",
        "",
        "| turn type | cold | status | elapsed (ms) |",
        "|---|---|---|---|",
    ]
    for s in results:
        lines.append(f"| {s.turn_type} | {s.is_cold} | {s.status} | {s.elapsed_ms:.0f} |")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Measure ingest/query/both turn latency against a deployed app (T12)."
    )
    parser.add_argument("--url", required=True, help="base URL of the app to measure")
    parser.add_argument("--samples", type=int, default=20, help="samples per turn type")
    parser.add_argument("--out", default="docs/latency.md", help="report output path")
    parser.add_argument(
        "--i-understand-this-mutates-production",
        dest="confirmed",
        action="store_true",
        help="required: this creates a real account and real memory rows on --url",
    )
    args = parser.parse_args()

    if not args.confirmed:
        print(
            "Refusing to run: this creates a real account and real memory rows on the target "
            "system. Re-run with --i-understand-this-mutates-production once you mean it.",
            file=sys.stderr,
        )
        raise SystemExit(2)

    results = run(args.url, args.samples)
    summary = summarize(results)
    write_report(args.out, args.url, args.samples, results, summary)
    print(f"wrote {args.out}")
    for turn_type in _TURN_TYPES:
        s = summary[turn_type]
        print(f"  {turn_type}: p50={s['p50_ms']} p95={s['p95_ms']} failed={s['n_failed']}")


if __name__ == "__main__":
    main()
