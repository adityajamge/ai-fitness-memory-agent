/**
 * The active conversation thread.
 *
 * DESIGN.md keeps this product deliberately single-threaded (§13: a thread switcher is out of
 * scope) — this module does not change that. It only makes the one active thread *restartable*:
 * persisted in localStorage so a page reload continues the same thread instead of silently
 * minting a new one, and reset on "New chat" so a user with months of real history can start a
 * visibly empty conversation without touching a single row of it. The old thread's turns and
 * memories are untouched in the database; only what "active" points at changes, and there is no
 * UI path back to a previous thread — starting a new one is a one-way door by design, same as a
 * real "New chat" button anywhere else.
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
