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
 *
 * **Why the result accumulates instead of tracking only the selected turn.** The hydration
 * request is per *selected* turn (see `useSelectedTrace`), but chips render for every turn on
 * screen — including ones that were selected earlier in the session. Returning only the latest
 * batch made a turn's chips flip from resolved back to "evidence not loaded" the moment selection
 * moved to a different turn (e.g. sending the next message), even though the rows had already
 * been fetched once. Memory ids are globally unique, so merging batches into one growing cache
 * is safe and keeps every previously-hydrated turn resolved for the rest of the session.
 */

import { useEffect, useMemo, useState } from "react";
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

  const [cache, setCache] = useState<{ rows: Map<string, MemoryRow>; missing: Set<string> }>(
    () => ({ rows: new Map(), missing: new Set() }),
  );

  useEffect(() => {
    if (!data) return;
    setCache((prev) => {
      const rows = new Map(prev.rows);
      for (const row of data.memories) rows.set(row.id, row);
      const missing = new Set(prev.missing);
      for (const id of data.missing) missing.add(id);
      // Both only ever grow, so an unchanged size means nothing new landed — bail out with the
      // same reference rather than forcing a re-render for a turn whose batch was cached already.
      if (rows.size === prev.rows.size && missing.size === prev.missing.size) return prev;
      return { rows, missing };
    });
  }, [data]);

  return useMemo(
    () => ({
      rows: cache.rows,
      missing: cache.missing,
      isHydrating: ids.length > 0 && isPending,
    }),
    [cache, ids.length, isPending],
  );
}
