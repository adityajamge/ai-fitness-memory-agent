/**
 * One batch hydration per turn, shared by the chips and the evidence pane (T16).
 *
 * **Why a hook and not two fetches.** The conversation needs hydrated rows to label its citation
 * chips, and the pane needs them to show payloads. Fetching in both places would double the
 * round trips for identical data, and cross-region latency dominates this system — this is the
 * exact N+1 that design rule 18 forbids.
 *
 * **Why `citable_ids` and not `trace.evidence`.** The citable set is deliberately wider: an
 * aggregate's contributing memories are citable but never appear as evidence snapshots, because
 * assembly is pure and does not hydrate them (ADR-14.7). Hydrating from `evidence` alone would
 * leave a correctly-cited aggregate row showing "memory unavailable".
 */

import { useMemo } from "react";
import { useMemories } from "@/api/queries";
import type { EvidenceTrace, MemoryRow } from "@/api/schemas";

export interface TurnEvidence {
  rows: Map<string, MemoryRow>;
  missing: Set<string>;
  isHydrating: boolean;
}

export function useTurnEvidence(trace: EvidenceTrace | null): TurnEvidence {
  // Sorted so the query key is stable regardless of the order the engine emitted them; an
  // unstable key would refetch the same rows on every render.
  const ids = useMemo(
    () => (trace ? [...trace.citable_ids].sort() : []),
    [trace],
  );

  const { data, isPending } = useMemories(ids);

  return useMemo(
    () => ({
      rows: new Map((data?.memories ?? []).map((row) => [row.id, row])),
      missing: new Set(data?.missing ?? []),
      isHydrating: ids.length > 0 && isPending,
    }),
    [data, ids.length, isPending],
  );
}
