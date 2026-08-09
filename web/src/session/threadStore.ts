/**
 * The active conversation thread.
 *
 * Exactly one thread is ever open in the UI at a time — this module tracks *which* one, not a
 * set. Persisted in localStorage so a page reload continues the same thread instead of silently
 * minting a new one. "New chat" mints a fresh id (`startNewThread`); the sidebar (added
 * 2026-08-09, §16 Decisions Log, un-deferring §13's "multi-thread conversation UI") points the
 * same active-thread slot at an EXISTING id instead (`setActiveThread`) — same mechanism, the
 * difference is only where the id comes from. Either way, switching never touches a row: the
 * previously active thread's turns and memories are exactly as they were, just no longer what
 * "active" points at.
 */

const STORAGE_KEY = "ayumind.thread_id";

function mintThreadId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  // Fallback for a non-secure context, where `crypto.randomUUID` is unavailable — good enough
  // for a client-side conversation handle that only ever needs to be unique, not unguessable.
  return `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

/** The current thread id, minting one on first-ever call so every session has one from the
 * start rather than lazily creating it on the first sent message. */
export function getActiveThreadId(): string {
  if (typeof window === "undefined") return mintThreadId();
  const existing = window.localStorage.getItem(STORAGE_KEY);
  if (existing) return existing;
  const id = mintThreadId();
  window.localStorage.setItem(STORAGE_KEY, id);
  return id;
}

/** Starts a fresh thread and returns its id — the whole of "New chat". */
export function startNewThread(): string {
  const id = mintThreadId();
  window.localStorage.setItem(STORAGE_KEY, id);
  return id;
}

/** Points the active-thread slot at an existing id — the whole of "load this thread from the
 * sidebar". No minting, no network call: the id is already real (it came from `GET /api/threads`
 * or was already active), so this is pure bookkeeping for which thread the rest of the app reads. */
export function setActiveThread(threadId: string): void {
  window.localStorage.setItem(STORAGE_KEY, threadId);
}
