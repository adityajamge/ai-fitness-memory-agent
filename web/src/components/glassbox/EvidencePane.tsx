/**
 * The memory engine pane — DESIGN.md §9.
 *
 * Not a debug panel a curious user can open. It is **always visible on desktop** and follows the
 * conversation without being asked, because the product's claim is that memory powers every
 * response, and a hidden pane makes that a claim rather than a demonstration.
 *
 * The one bug this component must never have: **showing the previous turn's evidence beside a new
 * answer.** That would make the glass box lie while looking authoritative, which is worse than
 * having no glass box. Hence a null trace renders "no context assembled for this turn" rather
 * than keeping the last rows on screen.
 *
 * Receives its hydrated rows rather than fetching them (see `useTurnEvidence`): the same batch
 * serves the chips in the conversation, and fetching here too would be the N+1 that T16 exists to
 * prevent.
 */

import { useEffect, useRef } from "react";
import { useReducedMotion } from "motion/react";
import type { EvidenceTrace, MemoryRow } from "@/api/schemas";
import { EmptyState } from "@/components/state/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";
import { EvidenceRow } from "./EvidenceRow";
import { RetrievalQueries } from "./RetrievalQueries";

export interface EvidencePaneProps {
  trace: EvidenceTrace | null;
  rows: Map<string, MemoryRow>;
  isHydrating: boolean;
  missingCount: number;
  isBusy: boolean;
  hasTurns: boolean;
  /** The memory a citation chip is currently pointing at, or null. */
  activeId: string | null;
}

export function EvidencePaneBody({
  trace,
  rows,
  isHydrating,
  missingCount,
  hasTurns,
  activeId,
}: Omit<EvidencePaneProps, "isBusy">) {
  const reduce = useReducedMotion();
  const activeRef = useRef<HTMLLIElement>(null);

  // The receiving half of the signature choreography (§5.6): the chip activates, this row
  // highlights, and the pane scrolls it into view. `nearest` rather than `center` so a row
  // already visible does not jump for no reason.
  useEffect(() => {
    if (!activeId || !activeRef.current) return;
    activeRef.current.scrollIntoView({
      behavior: reduce ? "auto" : "smooth",
      block: "nearest",
    });
  }, [activeId, reduce]);

  if (!hasTurns) {
    return (
      <EmptyState
        size="pane"
        title="Nothing retrieved yet."
        body="When you ask a question, every row the answer used will appear here."
      />
    );
  }

  /**
   * Every row the answer was allowed to cite, in one list.
   *
   * **`trace.evidence` alone is not enough.** An aggregate's contributing memories are citable but
   * never appear as evidence snapshots, because assembly is pure and does not hydrate them
   * (ADR-14.7). Verified live: "how much protein did I eat today?" returns 1 citable id and **0**
   * evidence snapshots. Rendering only snapshots would leave that chip with nothing to highlight,
   * and aggregates are exactly the money-shot query type — so the citable rows are folded in from
   * the hydration, adapted to the same shape.
   */
  const displayed = trace
    ? [
        ...trace.evidence,
        ...trace.citable_ids
          .filter((id) => !trace.evidence.some((e) => e.id === id))
          .map((id) => rows.get(id))
          .filter((row): row is MemoryRow => Boolean(row))
          .map((row) => ({
            id: row.id,
            type: row.type,
            event_time: row.event_time,
            confidence: row.confidence,
            provenance: row.provenance,
            summary: row.summary,
          })),
      ]
    : [];

  if (!trace || displayed.length === 0) {
    // Honest rather than stale. Stage (G) is best-effort and an ingest turn assembles no
    // context, so "none" is a real answer — not an error, and not a reason to show old rows.
    return (
      <>
        <p className="px-1 py-4 text-meta text-faint">no context assembled for this turn</p>
        {trace && <RetrievalQueries steps={trace.retrieval_steps} />}
      </>
    );
  }

  return (
    <>
      <ul className="flex flex-col gap-2">
        {displayed.map((evidence, i) => (
          <EvidenceRow
            key={evidence.id}
            ref={activeId === evidence.id ? activeRef : undefined}
            evidence={evidence}
            row={rows.get(evidence.id)}
            isActive={activeId === evidence.id}
            index={i}
          />
        ))}
      </ul>

      {isHydrating && <Skeleton className="mt-2 h-16 w-full" />}

      {/* §6.11 partial hydration: render what resolved and say so, rather than failing the pane
          over one stale chip. */}
      {missingCount > 0 && (
        <p className="mt-2 px-1 text-meta text-faint">
          {missingCount} cited {missingCount === 1 ? "memory is" : "memories are"} no longer
          available
        </p>
      )}

      <RetrievalQueries steps={trace.retrieval_steps} />

      {/* Insight lineage is RENDERED, never cited (Q1, resolved narrow): the narrator saw the
          hypothesis, not the rows beneath it, so these IDs are deliberately absent from the
          citable set and never become chips. */}
      {trace.insights.length > 0 && (
        <div className="mt-3 border-t border-border pt-3">
          <p className="font-mono text-micro uppercase tracking-[0.08em] text-faint">
            Insights consulted
          </p>
          <ul className="mt-2 flex flex-col gap-2">
            {trace.insights.map((insight) => (
              <li key={insight.id} className="rounded-md border border-border bg-surface-2 p-3">
                <p className="text-dense text-foreground">{insight.hypothesis}</p>
                <p className="mt-1.5 font-mono text-micro text-faint">
                  {insight.evidence_ids.length} supporting{" "}
                  {insight.evidence_ids.length === 1 ? "memory" : "memories"} · lineage shown,
                  not cited
                </p>
              </li>
            ))}
          </ul>
        </div>
      )}
    </>
  );
}

/** Desktop shell: fixed 420px column with its own header. */
export function EvidencePane(props: EvidencePaneProps) {
  return (
    <aside
      aria-label="Memory engine"
      className="flex h-full flex-col border-l border-border bg-surface"
    >
      <div className="flex shrink-0 items-center gap-2 border-b border-border px-4 py-3">
        <h2 className="font-mono text-micro uppercase tracking-[0.08em] text-faint">
          Memory Engine
        </h2>
        <span className="rounded-xs border border-border px-1.5 py-0.5 font-mono text-micro uppercase tracking-[0.08em] text-muted-foreground">
          {props.isBusy ? "working" : "following conversation"}
        </span>
      </div>

      <div className="flex-1 overflow-y-auto p-3">
        <EvidencePaneBody {...props} />
      </div>
    </aside>
  );
}
