/**
 * Memory receipt — DESIGN.md §6.7.
 *
 * Moment-to-moment proof that talking *is* logging. This is the component that makes the product's
 * central claim visible, and in the first-run sequence (§9.1) it is the payoff.
 *
 * Every value is mono because every value came out of the database (§4.1). The `✦` is the only
 * place outside citation affordances where `--signal` is allowed, because a receipt **is**
 * evidence — of a write.
 *
 * The failure receipt is styled with the same care as the success one on purpose. Never-lose-input
 * (ADR-13.5) is more impressive on first contact than a clean parse, and a state that looks
 * half-designed reads as a bug rather than a guarantee.
 */

import { m, useReducedMotion } from "motion/react";
import type { Receipt as ReceiptData } from "@/api/schemas";
import { cn } from "@/lib/utils";

function Tag({ children }: { children: React.ReactNode }) {
  return (
    <span className="rounded-xs border border-border bg-surface-2 px-1.5 py-0.5 font-mono text-meta text-foreground">
      {children}
    </span>
  );
}

export function Receipt({ receipt }: { receipt: ReceiptData }) {
  const reduce = useReducedMotion();
  const failed = receipt.parse_status !== "ok";
  const created = receipt.created.length;

  return (
    <m.div
      initial={reduce ? false : { opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: reduce ? 0 : 0.18, ease: [0.2, 0, 0, 1] }}
      className="flex flex-wrap items-center gap-1.5 text-meta text-muted-foreground"
    >
      <span className={cn("mr-0.5", failed ? "text-muted-foreground" : "text-signal")}>✦</span>

      {failed ? (
        <span>{receipt.message || "saved — parsing incomplete"}</span>
      ) : (
        <span>
          {created} {created === 1 ? "memory" : "memories"} created:
        </span>
      )}

      {receipt.created.map((ref) => (
        <Tag key={ref.id}>{ref.summary ?? ref.type}</Tag>
      ))}

      {/* Honest about a real gap: the row exists, the vector may not yet. Backfill runs on the
          next ingest, so this is a state the user can see rather than a silent inconsistency. */}
      {receipt.created.some((r) => r.embedding_pending) ? (
        <span className="font-mono text-faint">embedding pending</span>
      ) : (
        created > 0 && <span className="font-mono text-faint">+ embedding</span>
      )}

      {/* Separate from `created`, never folded in: the user reported the memory, whereas an
          insight is a claim the engine made about it (Phase 5 §4.8). */}
      {receipt.insights.length > 0 && (
        <>
          <span aria-hidden="true" className="text-faint">·</span>
          <span className="text-signal">
            {receipt.insights.length} insight{receipt.insights.length === 1 ? "" : "s"} updated
          </span>
        </>
      )}
    </m.div>
  );
}
