/**
 * Light/dark theme — added 2026-08-09 (DESIGN.md §16 Decisions Log).
 *
 * Same shape as `session/sessionStore.ts`: a module-level store read through
 * `useSyncExternalStore`, so any component can subscribe without a provider to thread through
 * three route trees (app, landing, auth) that don't otherwise share a layout.
 *
 * **Default is the OS preference, not a hardcoded dark.** First visit follows
 * `prefers-color-scheme`; the moment someone uses the toggle, that choice is persisted in
 * localStorage and wins over the OS setting from then on, including if the OS setting later
 * changes underneath them — an explicit choice should not get silently overridden by a laptop
 * switching to night mode.
 *
 * **No FOUC.** This module runs the same `resolveTheme` logic `index.html`'s inline script
 * already ran synchronously before first paint (see that file) — this just brings React's world
 * into agreement with whatever the DOM attribute already says, rather than re-deciding and
 * risking a flash if the two ever disagreed.
 */

import { useSyncExternalStore } from "react";

export type Theme = "dark" | "light";

const STORAGE_KEY = "ayumind.theme";
const THEME_COLOR: Record<Theme, string> = { dark: "#0B0D10", light: "#EEF2F6" };

function isTheme(value: string | null): value is Theme {
  return value === "dark" || value === "light";
}

function systemTheme(): Theme {
  if (typeof window === "undefined") return "dark";
  return window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

function readStored(): Theme | null {
  if (typeof window === "undefined") return null;
  const stored = window.localStorage.getItem(STORAGE_KEY);
  return isTheme(stored) ? stored : null;
}

/** DOM writes, in one place: the `<html>` attribute CSS keys off, the `color-scheme` property for
 * native form controls (belt-and-braces — the CSS in theme.css already sets this per-theme too),
 * and the mobile browser-chrome color. */
function applyToDom(theme: Theme) {
  if (typeof document === "undefined") return;
  document.documentElement.dataset.theme = theme;
  document.documentElement.style.colorScheme = theme;
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", THEME_COLOR[theme]);
}

let theme: Theme = readStored() ?? systemTheme();
let hasExplicitChoice = readStored() !== null;
const listeners = new Set<() => void>();

function emit() {
  for (const listener of listeners) listener();
}

applyToDom(theme);

if (typeof window !== "undefined") {
  // Only relevant while nobody has ever toggled — the moment they do, `hasExplicitChoice` locks
  // this out, per the module doc's "should not get silently overridden" rule.
  window.matchMedia("(prefers-color-scheme: light)").addEventListener("change", (event) => {
    if (hasExplicitChoice) return;
    theme = event.matches ? "light" : "dark";
    applyToDom(theme);
    emit();
  });
}

export function setTheme(next: Theme) {
  theme = next;
  hasExplicitChoice = true;
  window.localStorage.setItem(STORAGE_KEY, next);
  applyToDom(next);
  emit();
}

export function toggleTheme() {
  setTheme(theme === "dark" ? "light" : "dark");
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

const getSnapshot = () => theme;

export function useTheme(): Theme {
  return useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
}
