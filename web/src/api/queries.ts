/**
 * TanStack Query hooks — the only way components read server state.
 *
 * Query keys follow `[resource, ...identifiers]` (frontend-guidelines §5). Consistency here is
 * what makes SSE invalidation a one-liner in M6: one `invalidateQueries({ queryKey: ["stats"] })`
 * updates the top bar, the timeline, and the insight count without any of them knowing about
 * each other.
 */

import { useMutation, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import {
  ApiError,
  getMemoriesBatch,
  getStats,
  getTimeline,
  getTrace,
  getTurns,
  sendMessage,
} from "./client";
import { markAuthenticated } from "@/session/sessionStore";

export const queryKeys = {
  stats: ["stats"] as const,
  timeline: ["timeline"] as const,
  // Keyed by thread so "New chat" (a new thread id) never reads the previous thread's cached
  // history — each thread gets its own cache entry under the shared "turns" prefix, which is
  // also what lets invalidateTurnDerivedQueries below invalidate all of them with one call.
  turns: (threadId?: string) => ["turns", threadId ?? "all"] as const,
  trace: (turnId: string) => ["trace", turnId] as const,
  memories: (ids: string[]) => ["memories", ...ids] as const,
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

/** Conversation history for one thread — the active thread, per `session/threadStore.ts`. Every
 * account has exactly one at a time; "New chat" swaps which thread is active rather than adding
 * a second one to read. */
export function useTurns(threadId: string) {
  return useQuery({ queryKey: queryKeys.turns(threadId), queryFn: () => getTurns({ threadId }) });
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
  // The bare "turns" prefix, not `queryKeys.turns(id)` for one specific thread: TanStack Query
  // matches by prefix, so this invalidates every thread's cached history in one call. There is
  // only ever one active thread in the UI, but stale entries for an abandoned thread (from
  // before a "New chat") should not linger as reusable cache either.
  void queryClient.invalidateQueries({ queryKey: ["turns"] });
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
 * through the SSE transport instead of a mutation. */
export function useInvalidateAfterTurn() {
  const queryClient = useQueryClient();
  return () => invalidateTurnDerivedQueries(queryClient);
}
