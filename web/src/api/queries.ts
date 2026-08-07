/**
 * TanStack Query hooks — the only way components read server state.
 *
 * Query keys follow `[resource, ...identifiers]` (frontend-guidelines §5). Consistency here is
 * what makes SSE invalidation a one-liner in M6: one `invalidateQueries({ queryKey: ["stats"] })`
 * updates the top bar, the timeline, and the insight count without any of them knowing about
 * each other.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
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
  turns: ["turns"] as const,
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

export function useTurns() {
  // Wrapped rather than passed by reference: Query calls queryFn with a context object, which
  // `getTurns` would read as its optional params argument.
  return useQuery({ queryKey: queryKeys.turns, queryFn: () => getTurns() });
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
 * Send a turn.
 *
 * On success every derived surface is invalidated together, because one message can change all
 * of them at once: an ingest turn creates memories (stats, timeline), records turns (history),
 * and may trigger consolidation that writes an insight (stats again). Invalidating individually
 * would let the top bar and the timeline disagree for a frame.
 */
export function useSendMessage() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ message, threadId }: { message: string; threadId?: string }) =>
      sendMessage(message, threadId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.stats });
      void queryClient.invalidateQueries({ queryKey: queryKeys.timeline });
      void queryClient.invalidateQueries({ queryKey: queryKeys.turns });
    },
  });
}
