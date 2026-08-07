/**
 * Session-expiry signal — DESIGN.md §6.11.1 (F-T4).
 *
 * A 401 can surface from any query or mutation, so the signal cannot live in a component. This is
 * a module-level store read through `useSyncExternalStore`: no provider ordering to get wrong, no
 * context threading, and the query client can report into it from `main.tsx` before React renders.
 *
 * **What this deliberately does NOT do is redirect.** A redirect throws away whatever the user had
 * typed, and never losing what you told it is the product's core promise (ADR-13.5). The composer
 * keeps its draft, the conversation stays on screen, and re-auth happens in a dialog over the app.
 */

import { useSyncExternalStore } from "react";

let expired = false;
let hasAuthenticated = false;
const listeners = new Set<() => void>();

function emit() {
  for (const listener of listeners) listener();
}

/** Called once a user-scoped request succeeds. This is what separates "expired" from "never
 * signed in" — without it, a logged-out visitor landing on /app would be told their session
 * ended, which is both wrong and confusing. */
export function markAuthenticated() {
  hasAuthenticated = true;
}

/**
 * Called by the query client's global error handlers (see `main.tsx`).
 *
 * A no-op until a request has succeeded at least once, so the 401 that simply means "you are not
 * signed in" routes to /login instead of raising an expiry notice. Idempotent, so a burst of
 * simultaneous 401s from parallel queries produces one notice rather than several.
 */
export function markSessionExpired() {
  if (expired || !hasAuthenticated) return;
  expired = true;
  emit();
}

/** Called after a successful re-auth. */
export function clearSessionExpired() {
  if (!expired) return;
  expired = false;
  emit();
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

const getSnapshot = () => expired;

export function useSessionExpired(): boolean {
  return useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
}
