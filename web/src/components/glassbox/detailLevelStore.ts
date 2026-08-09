/**
 * Plain-English vs raw-JSON preference for the evidence pane.
 *
 * Same shape as `theme/themeStore.ts` and `session/sessionStore.ts`: a module-level store read
 * through `useSyncExternalStore`, so every expanded evidence row shares one setting without
 * threading a provider through the pane, the mobile drawer, and the receipt.
 *
 * **Default is `plain`.** The raw payload is the proof, but it is not an explanation, and most
 * people reading this pane are not reading JSON. A technical reader flips it once and the
 * choice persists — the toggle lives inside each expanded row so it is where you need it, while
 * the *preference* is global, so nobody sets it row by row.
 */

import { useSyncExternalStore } from "react";

export type DetailLevel = "plain" | "raw";

const STORAGE_KEY = "ayumind.evidenceDetail";

function isDetailLevel(value: string | null): value is DetailLevel {
  return value === "plain" || value === "raw";
}

function readStored(): DetailLevel {
  if (typeof window === "undefined") return "plain";
  return isDetailLevel(window.localStorage.getItem(STORAGE_KEY))
    ? (window.localStorage.getItem(STORAGE_KEY) as DetailLevel)
    : "plain";
}

let level: DetailLevel = readStored();
const listeners = new Set<() => void>();

export function setDetailLevel(next: DetailLevel) {
  if (next === level) return;
  level = next;
  try {
    window.localStorage.setItem(STORAGE_KEY, next);
  } catch {
    // Private-browsing quota errors must not break the toggle; the choice simply
    // lasts for this session instead of persisting.
  }
  for (const listener of listeners) listener();
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

const getSnapshot = () => level;
/** Server snapshot: the default, so a hydration pass never disagrees with first paint. */
const getServerSnapshot = () => "plain" as DetailLevel;

export function useDetailLevel(): DetailLevel {
  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}
