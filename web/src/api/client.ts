/**
 * The single fetch boundary.
 *
 * Every response is validated here (`schemas.ts`) so a backend contract change surfaces as one
 * located error rather than as `undefined` deep inside a component. Nothing else in the app may
 * call `fetch` directly.
 *
 * Same-origin by design: FastAPI serves this SPA's static assets and the API from one container
 * (ADR-13.3/13.7), so there is no base URL, no CORS, and no API host to configure. Session auth
 * rides on the cookie, hence `credentials: "same-origin"`.
 */

import type { z } from "zod";
import {
  AuthResponse,
  BatchMemoriesResponse,
  ChatResponse,
  DayMemoriesResponse,
  StatsResponse,
  ThreadsResponse,
  TimelineResponse,
  TraceResponse,
  TurnsResponse,
} from "./schemas";

/** Thrown for any non-2xx response. `status` lets callers distinguish 401 from 404 from 5xx. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/** Thrown when the server answered 2xx but the body did not match the contract. */
export class ContractError extends Error {
  constructor(
    readonly path: string,
    readonly issues: unknown,
  ) {
    super(`response from ${path} did not match the expected contract`);
    this.name = "ContractError";
  }
}

async function request<S extends z.ZodType>(
  path: string,
  schema: S,
  init?: RequestInit,
): Promise<z.infer<S>> {
  // Built rather than spread with a conditional `headers`, because under
  // `exactOptionalPropertyTypes` an explicit `undefined` is not the same as an absent key.
  const requestInit: RequestInit = { credentials: "same-origin", ...init };
  if (init?.body) requestInit.headers = { "content-type": "application/json" };

  const response = await fetch(path, requestInit);

  if (!response.ok) {
    // Prefer the API's own `detail`; fall back to the status text rather than inventing copy.
    let detail = response.statusText;
    try {
      const body = (await response.json()) as { detail?: string };
      if (body?.detail) detail = body.detail;
    } catch {
      /* non-JSON error body — the status text stands */
    }
    throw new ApiError(response.status, detail);
  }

  const parsed = schema.safeParse(await response.json());
  if (!parsed.success) throw new ContractError(path, parsed.error.issues);
  return parsed.data;
}

/* ── the glass-box read surface ──────────────────────────────────────────────────────────── */

/** One turn's glass box. 404 covers "no such turn", "no trace", and "belongs to someone else" —
 * all indistinguishable by design (I-28), so the UI must not try to tell them apart. */
export const getTrace = (turnId: string) =>
  request(`/api/turns/${turnId}/trace`, TraceResponse);

export const getTurns = (params?: { threadId?: string; limit?: number }) => {
  const q = new URLSearchParams();
  if (params?.threadId) q.set("thread_id", params.threadId);
  if (params?.limit) q.set("limit", String(params.limit));
  const qs = q.toString();
  return request(`/api/turns${qs ? `?${qs}` : ""}`, TurnsResponse);
};

/**
 * Hydrate many memory IDs in **one** round trip (T16).
 *
 * Design rule 18: N+1 at this boundary is a bug, not a style preference. Cross-region round
 * trips dominate this system, so a per-chip fetch is the difference between a responsive
 * evidence pane and an unusable one. Collect the IDs, call this once.
 */
export const getMemoriesBatch = (ids: string[]) =>
  request(`/api/memories/batch`, BatchMemoriesResponse, {
    method: "POST",
    body: JSON.stringify({ ids }),
  });

/**
 * Top-bar stats — and the app's **session probe**.
 *
 * There is no `/api/auth/me`. This endpoint is user-scoped and 401s when logged out, and the app
 * shell needs the numbers on load regardless, so one request answers both "am I signed in?" and
 * "what do I show in the top bar?". Adding a dedicated endpoint would cost a round trip to learn
 * something this one already tells us.
 */
export const getStats = () => request(`/api/stats`, StatsResponse);

export const getTimeline = () => request(`/api/timeline`, TimelineResponse);

/** Every memory logged on one day (§9 "click a timeline day") — a raw listing, not a turn's
 * retrieval trace, since a bar has no query behind it, just the rows that made it that height. */
export const getMemoriesByDay = (day: string) =>
  request(`/api/memories/by-day/${day}`, DayMemoriesResponse);

/** The sidebar's data — this user's conversations, most recently active first. `thread_id` is
 * already the raw id (the API strips the internal `user_id:` namespace), the same string this
 * client hands `getTurns`/`POST /api/chat`. */
export const getThreads = (limit?: number) => {
  const qs = limit ? `?limit=${limit}` : "";
  return request(`/api/threads${qs}`, ThreadsResponse);
};

/* ── auth ────────────────────────────────────────────────────────────────────────────────── */

/** 409 when the email already has an account; 422 when the address or password fails validation. */
export const signup = (email: string, password: string) =>
  request(`/api/auth/signup`, AuthResponse, {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });

/** 401 on bad credentials. The UI must not say *which* field was wrong (§6.17). */
export const login = (email: string, password: string) =>
  request(`/api/auth/login`, AuthResponse, {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });

export const logout = async (): Promise<void> => {
  await fetch("/api/auth/logout", { method: "POST", credentials: "same-origin" });
};

/* ── conversation ────────────────────────────────────────────────────────────────────────── */

/** One turn: plan → (ingest) → (retrieve) → assemble → narrate. Slow by nature; the UI shows
 * staged progress (§6.10) rather than a spinner, and never blocks the composer. */
export const sendMessage = (message: string, threadId?: string) =>
  request(`/api/chat`, ChatResponse, {
    method: "POST",
    body: JSON.stringify(threadId ? { message, thread_id: threadId } : { message }),
  });
