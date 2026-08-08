/**
 * One evidence row — DESIGN.md §5.9, §6.4, §9.
 *
 * This is where "nothing is distinguished by hue alone" (WCAG 1.4.1) stops being a principle and
 * becomes code:
 *
 * - **Provenance is fill vs outline.** `live` is a filled tag; `reconstructed` is a hairline
 *   dashed outline. Identical in grayscale, in a screenshot, and to a colorblind reader.
 * - **Confidence is a four-segment meter**, not a hue. 0.9 reads as four filled segments whether
 *   or not color renders at all, and it carries an explicit label with the exact value.
 *
 * Both were possible to do with color in half the code. Neither would have survived a grayscale
 * screenshot in a judging deck.
 *
 * The row expands to its raw payload, which is the bottom of the glass box: past this there is
 * nothing left to show, because this *is* the database row.
 */

import { forwardRef, useState } from "react";
import { ChevronRight } from "lucide-react";
import { m, useReducedMotion } from "motion/react";
import type { EvidenceSnapshot, MemoryRow } from "@/api/schemas";
import { cn } from "@/lib/utils";

const SEGMENTS = 4;

/** Exported for reuse — insight lineage cards (EvidencePane) show the same meter for
 * `pattern_strength` (§9 "click an insight"), which is a 0–1 score exactly like confidence. */
export function ConfidenceMeter({ value }: { value: number }) {
  const filled = Math.max(1, Math.round(value * SEGMENTS));
  return (
    <span
      className="inline-flex items-center gap-px align-middle"
      title={`confidence ${value.toFixed(2)}`}
      role="img"
      aria-label={`confidence ${value.toFixed(2)} of 1`}
    >
      {Array.from({ length: SEGMENTS }, (_, i) => (
        <span
          key={i}
          className={cn(
            "h-2.5 w-[3px] rounded-xs",
            i < filled ? "bg-muted-foreground" : "bg-surface-3",
          )}
        />
      ))}
    </span>
  );
}

function ProvenanceTag({ provenance }: { provenance: string }) {
  const isLive = provenance === "live";
  return (
    <span
      className={cn(
        "rounded-xs px-1.5 py-0.5 font-mono text-micro uppercase tracking-[0.08em]",
        isLive
          ? "bg-surface-3 text-muted-foreground"
          : "border border-dashed border-border text-faint",
      )}
      title={
        isLive
          ? "captured as it happened"
          : "rebuilt from records — the timestamp is an estimate"
      }
    >
      {provenance}
    </span>
  );
}

export interface EvidenceRowProps {
  evidence: EvidenceSnapshot;
  /** The hydrated row, when available — supplies the payload the snapshot deliberately omits. */
  row?: MemoryRow | undefined;
  /** True when a citation chip pointing at this memory is active (§5.6 choreography). */
  isActive?: boolean;
  /** Position in the list, used for the staggered entrance. The animation lives on the <li>
   * itself: a wrapper <div> between <ul> and <li> is invalid markup, and axe flags it. */
  index?: number;
}

export const EvidenceRow = forwardRef<HTMLLIElement, EvidenceRowProps>(function EvidenceRow(
  { evidence, row, isActive = false, index = 0 },
  ref,
) {
  const [isOpen, setOpen] = useState(false);
  const reduce = useReducedMotion();
  const when = new Date(evidence.event_time);

  return (
    <m.li
      ref={ref}
      initial={reduce ? false : { opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{
        duration: reduce ? 0 : 0.24,
        // Capped: a 40-row result staggered at 40ms would take 1.6s to finish arriving.
        delay: reduce ? 0 : Math.min(index, 6) * 0.04,
        ease: [0.2, 0, 0, 1],
      }}
      className={cn(
        "rounded-md border bg-surface-2 transition-colors duration-180 ease-out",
        isActive
          ? // The receiving half of the one choreographed moment in the product.
            "border-signal bg-signal-dim"
          : "border-border hover:bg-surface-3",
      )}
    >
      <div className="px-3 py-2.5">
        <div className="flex items-baseline justify-between gap-3">
          <span className="text-dense text-foreground">{evidence.summary ?? evidence.type}</span>
          {/* Mono + tabular: a database timestamp, and dates in a column must align. */}
          <time
            dateTime={evidence.event_time}
            className="shrink-0 font-mono text-meta text-muted-foreground"
          >
            {when.toLocaleDateString(undefined, { month: "short", day: "numeric" })}
          </time>
        </div>

        <div className="mt-2 flex items-center gap-2">
          <ProvenanceTag provenance={evidence.provenance} />
          <ConfidenceMeter value={evidence.confidence} />

          {row ? (
            <button
              type="button"
              onClick={() => setOpen((v) => !v)}
              aria-expanded={isOpen}
              aria-label={isOpen ? "Hide raw payload" : "Show raw payload"}
              className="ml-auto flex items-center gap-1 font-mono text-micro text-faint transition-colors duration-120 hover:text-muted-foreground"
            >
              <ChevronRight
                className={cn(
                  "size-3 transition-transform duration-180 ease-out",
                  isOpen && "rotate-90",
                )}
                strokeWidth={1.5}
                aria-hidden="true"
              />
              {evidence.id.slice(0, 8)}
            </button>
          ) : (
            <span className="ml-auto font-mono text-micro text-faint">
              {evidence.id.slice(0, 8)}
            </span>
          )}
        </div>
      </div>

      {isOpen && row && (
        <div className="border-t border-border px-3 py-2.5">
          <pre className="overflow-x-auto">
            <code className="font-mono text-micro leading-relaxed text-muted-foreground">
              {JSON.stringify(row.payload, null, 2)}
            </code>
          </pre>
          {/* Surfaced rather than hidden: a superseded row is still evidence, and knowing it was
              replaced is part of the honest record. */}
          {row.status !== "active" && (
            <p className="mt-2 font-mono text-micro text-faint">status: {row.status}</p>
          )}
        </div>
      )}
    </m.li>
  );
});
