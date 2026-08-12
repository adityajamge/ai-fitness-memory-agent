/**
 * TanStack Query hooks — the only way components read server state.
 *
 * Query keys follow `[resource, ...identifiers]` (frontend-guidelines §5). Consistency here is
 * what makes SSE invalidation a one-liner in M6: one `invalidateQueries({ queryKey: ["stats"] })`
 * updates the top bar, the timeline, and the insight count without any of them knowing about
 * each other.
 */

import { useCallback } from "react";
import { useMutation, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import {
  ApiError,
  getMemoriesBatch,
  getMemoriesByDay,
  getProfile,
  getStats,
  getThreads,
  getTimeline,
  getToday,
  getTrace,
  getTurns,
  sendMessage,
  submitOnboarding,
  updateProfile,
  type OnboardingInput,
  type ProfileEditInput,
} from "./client";
import { markAuthenticated } from "@/session/sessionStore";

export const queryKeys = {
  stats: ["stats"] as const,
  timeline: ["timeline"] as const,
  today: ["today"] as const,
  // Keyed by thread so "New chat" (a new thread id) never reads the previous thread's cached
  // history — each thread gets its own cache entry under the shared "turns" prefix, which is
  // also what lets invalidateTurnDerivedQueries below invalidate all of them with one call.
  turns: (threadId?: string) => ["turns", threadId ?? "all"] as const,
  trace: (turnId: string) => ["trace", turnId] as const,
  memories: (ids: string[]) => ["memories", ...ids] as const,
  memoriesByDay: (day: string) => ["memories-by-day", day] as const,
  threads: ["threads"] as const,
  profile: ["profile"] as const,
};

/** True when a rejection is specifically "not signed in", so callers can branch without
 * string-matching an error message. */
export const isUnauthorized = (error: unknown): boolean =>
  error instanceof ApiError && error.status === 401;

/**
 * Top-bar stats, and the app's session probe (see `client.getStats`).
 *
 * `retry: false` matters here beyond performance: this query's 401 *is* the signal that the
 * session ended, and retrying would delay the notice by seconds while the user types into a
 * composer whose send is already doomed.
 */
export function useStats() {
  return useQuery({
    queryKey: queryKeys.stats,
    queryFn: async () => {
      const stats = await getStats();
      // First success is what proves a session existed, so a later 401 can be told apart from
      // "never signed in" (see sessionStore).
      markAuthenticated();
      return stats;
    },
    retry: false,
  });
}

export function useTimeline() {
  return useQuery({ queryKey: queryKeys.timeline, queryFn: getTimeline });
}

/**
 * The home screen's single read.
 *
 * `staleTime: 0` overrides the app-wide 60s default, and the reason is specific to this query:
 * every other cached thing here is immutable once written (a trace never changes, a memory row
 * only changes when superseded), whereas Today is a *view of now* — it rolls over at local
 * midnight and moves every time a meal is logged. A minute of staleness on a trace is free; a
 * minute of staleness on "today's protein" is a wrong number on the most-seen screen.
 */
export function useToday() {
  return useQuery({ queryKey: queryKeys.today, queryFn: getToday, staleTime: 0 });
}

/** Conversation history for one thread — the active thread, per `session/threadStore.ts`. Only
 * one thread is ever open in the UI at a time; the sidebar (`useThreads` below) is what lets a
 * past thread become the active one again, not a second simultaneous read. */
export function useTurns(threadId: string) {
  return useQuery({ queryKey: queryKeys.turns(threadId), queryFn: () => getTurns({ threadId }) });
}

/** The sidebar's data — this account's threads, most recently active first. */
export function useThreads() {
  return useQuery({ queryKey: queryKeys.threads, queryFn: () => getThreads() });
}

/** A turn's glass box. Enabled only for turns that have one — stage (G) is best-effort, and a
 * turn without a trace is honest rather than an error. */
export function useTrace(turnId: string | null) {
  return useQuery({
    queryKey: queryKeys.trace(turnId ?? ""),
    queryFn: () => getTrace(turnId as string),
    enabled: Boolean(turnId),
  });
}

/** Hydrate a whole citable set in ONE request (T16). Never call this per chip. */
export function useMemories(ids: string[]) {
  return useQuery({
    queryKey: queryKeys.memories(ids),
    queryFn: () => getMemoriesBatch(ids),
    enabled: ids.length > 0,
  });
}

/** Every memory logged on one day — the timeline bar's drill-down (§9 "click a timeline day").
 * A raw listing, not a turn's trace, so it is fetched independently of `useSelectedTrace`. */
export function useMemoriesByDay(day: string | null) {
  return useQuery({
    queryKey: queryKeys.memoriesByDay(day ?? ""),
    queryFn: () => getMemoriesByDay(day as string),
    enabled: Boolean(day),
  });
}

/**
 * Every derived surface, invalidated together, because one message can change all of them at
 * once: an ingest turn creates memories (stats, timeline), records turns (history), and may
 * trigger consolidation that writes an insight (stats again). Invalidating individually would
 * let the top bar and the timeline disagree for a frame.
 *
 * Shared between `useSendMessage` (the plain transport) and the SSE transport
 * (`AppScreen`'s `useInvalidateAfterTurn`) — the two must leave the cache in the same state, or
 * falling back from one to the other mid-session would be observable as a UI inconsistency.
 */
function invalidateTurnDerivedQueries(queryClient: QueryClient) {
  void queryClient.invalidateQueries({ queryKey: queryKeys.stats });
  void queryClient.invalidateQueries({ queryKey: queryKeys.timeline });
  // Today reads targets, both day totals, coverage, the newest insight and the recent strip —
  // an ingest turn can move every one of them at once, so it invalidates with the rest rather
  // than waiting for a remount.
  void queryClient.invalidateQueries({ queryKey: queryKeys.today });
  // Prefix match across every cached day, the same reason the "turns" invalidation below is a
  // bare prefix: a new memory can land on a day whose list is already cached from an earlier
  // timeline click.
  void queryClient.invalidateQueries({ queryKey: ["memories-by-day"] });
  // The bare "turns" prefix, not `queryKeys.turns(id)` for one specific thread: TanStack Query
  // matches by prefix, so this invalidates every thread's cached history in one call. There is
  // only ever one active thread in the UI, but stale entries for an abandoned thread (from
  // before a "New chat") should not linger as reusable cache either.
  void queryClient.invalidateQueries({ queryKey: ["turns"] });
  // A turn can start a brand-new thread or bump an existing one to the top of "most recently
  // active" — the sidebar list is stale either way.
  void queryClient.invalidateQueries({ queryKey: queryKeys.threads });
}

/** Send a turn over the plain, always-available transport (the SSE fallback target). */
export function useSendMessage() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ message, threadId }: { message: string; threadId?: string }) =>
      sendMessage(message, threadId),
    onSuccess: () => invalidateTurnDerivedQueries(queryClient),
  });
}

/** The same cache invalidation `useSendMessage` does on success, for callers driving the turn
 * through the SSE transport instead of a mutation.
 *
 * Memoized (`useCallback`, not a fresh closure per render): `AppScreen` puts this in a dependency
 * array for its own memoized send handler, and an unstable reference here would recreate that
 * handler — and every memoized child it's passed to — on every unrelated re-render (typing in the
 * composer, most of all). */
export function useInvalidateAfterTurn() {
  const queryClient = useQueryClient();
  return useCallback(() => invalidateTurnDerivedQueries(queryClient), [queryClient]);
}

/* ── profile (ADR-17) ─────────────────────────────────────────────────────────────────────── */

/** The onboarding screen and Profile settings share this one query — the intake screen reads
 * it to decide whether it should even render (§6.19: `/onboarding` redirects once
 * `has_onboarded` is true, like a used-up link). */
export function useProfile() {
  return useQuery({ queryKey: queryKeys.profile, queryFn: getProfile });
}

export function useSubmitOnboarding() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: OnboardingInput) => submitOnboarding(body),
    onSuccess: (profile) => queryClient.setQueryData(queryKeys.profile, profile),
  });
}

export function useUpdateProfile() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: ProfileEditInput) => updateProfile(body),
    onSuccess: (profile) => {
      queryClient.setQueryData(queryKeys.profile, profile);
      // A profile edit can move a target (directly, or by recomputation after a weight change)
      // and `weight_kg` writes a real memory — so Today's targets, weight and stats are all
      // stale. Invalidated rather than patched: the server owns those numbers.
      void queryClient.invalidateQueries({ queryKey: queryKeys.today });
      void queryClient.invalidateQueries({ queryKey: queryKeys.stats });
    },
  });
}
