/**
 * The memory engine pane — DESIGN.md §9.
 *
 * Not a debug panel a curious user can open. It is **always visible on desktop** and follows the
 * conversation without being asked, because the product's claim is that memory powers every
 * response, and a hidden pane makes that a claim rather than a demonstration.
 *
 * The one bug this component must never have: **showing the previous turn's evidence beside a new
 * answer.** That would make the glass box lie while looking authoritative, which is worse than
 * having no glass box. Hence `trace === null` renders "no context assembled for this turn" rather
 * than keeping the last rows on screen.
 */

import { m, useReducedMotion } from "motion/react";
import type { EvidenceTrace } from "@/api/schemas";
import { EmptyState } from "@/components/state/EmptyState";
import { EvidenceRow } from "./EvidenceRow";

/** Cap on how many rows get a staggered entrance. Beyond this they appear together — a 40-row
 * result staggered at 40ms would take 1.6s to finish arriving, which reads as slow, not smooth. */
const MAX_STAGGER = 6;

export function EnginePane({
  trace,
  isBusy,
  hasTurns,
}: {
  trace: EvidenceTrace | null;
  isBusy: boolean;
  hasTurns: boolean;
}) {
  const reduce = useReducedMotion();

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
          {isBusy ? "working" : "following conversation"}
        </span>
      </div>

      <div className="flex-1 overflow-y-auto p-3">
        {!hasTurns ? (
          <EmptyState
            size="pane"
            title="Nothing retrieved yet."
            body="When you ask a question, every row the answer used will appear here."
          />
        ) : !trace || trace.evidence.length === 0 ? (
          // Honest rather than stale. Stage (G) is best-effort and an ingest turn assembles no
          // context, so "none" is a real answer — not an error, and not a reason to show old rows.
          <p className="px-1 py-4 text-meta text-faint">
            no context assembled for this turn
          </p>
        ) : (
          <>
            <ul className="flex flex-col gap-2">
              {trace.evidence.map((evidence, i) => (
                <m.div
                  key={evidence.id}
                  initial={reduce ? false : { opacity: 0, y: 4 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{
                    duration: reduce ? 0 : 0.24,
                    delay: reduce ? 0 : Math.min(i, MAX_STAGGER) * 0.04,
                    ease: [0.2, 0, 0, 1],
                  }}
                >
                  <EvidenceRow evidence={evidence} />
                </m.div>
              ))}
            </ul>

            {/* The retrieval-query display lands in M6; the count is real now and comes from the
                trace, not from a placeholder. */}
            {trace.retrieval_steps.length > 0 && (
              <p className="mt-3 px-1 font-mono text-meta text-faint">
                {trace.retrieval_steps.length}{" "}
                {trace.retrieval_steps.length === 1 ? "query" : "queries"} ·{" "}
                {trace.citable_ids.length} citable
              </p>
            )}
          </>
        )}
      </div>
    </aside>
  );
}
