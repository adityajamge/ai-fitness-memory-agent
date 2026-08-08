/**
 * The trace for whichever turn the user is inspecting.
 *
 * Two sources, one shape:
 *
 * - **Live turns** carry their trace inline from `POST /api/chat`. Nothing to fetch.
 * - **History turns** (seeded from `GET /api/turns`) do not, so their trace is fetched by
 *   `turnId` from `GET /api/turns/{id}/trace` — the M3 endpoint built for exactly this.
 *
 * **Why this matters beyond convenience.** Without it, reloading the page leaves every past
 * answer inert: the chips render, but nothing resolves and nothing can be inspected. The
 * product's promise is that the user can always see where an answer came from, and "only the
 * most recent one" is not that promise.
 *
 * The fetch is per *selected* turn, not per turn on screen — one request when the user asks for
 * one, never N on render.
 */

import { useTrace } from "@/api/queries";
import type { EvidenceTrace } from "@/api/schemas";
import type { ChatTurn } from "@/types/turn";

export interface SelectedTrace {
  trace: EvidenceTrace | null;
  isLoading: boolean;
}

export function useSelectedTrace(turn: ChatTurn | null): SelectedTrace {
  const inline = turn?.kind === "assistant" ? turn.trace : null;

  // Skip the request entirely when the turn already carries its trace, or has none to fetch.
  const fetchId = turn?.kind === "assistant" && !inline ? turn.turnId : null;
  const { data, isPending } = useTrace(fetchId);

  return {
    trace: inline ?? data?.trace ?? null,
    isLoading: Boolean(fetchId) && isPending,
  };
}
