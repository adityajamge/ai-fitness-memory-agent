/**
 * One evidence row — DESIGN.md §5.9, §6.4, §9.
 *
 * This is where "nothing is distinguished by hue alone" (WCAG 1.4.1) stops being a principle and
 * becomes code:
 *
 * - **Provenance is fill vs outline.** `live` is a filled tag; `reconstructed` is a hairline
 *   outline with a dashed leading rule. Identical in grayscale, in a screenshot, and to a
 *   colorblind reader.
 * - **Confidence is a four-segment meter**, not a hue. 0.9 reads as four filled segments whether
 *   or not color renders at all, and it carries an explicit `title` for the exact value.
 *
 * Both were possible to do with color in half the code. Neither would have survived a grayscale
 * screenshot in a judging deck.
 */

import type { EvidenceSnapshot } from "@/api/schemas";
import { cn } from "@/lib/utils";

const SEGMENTS = 4;

function ConfidenceMeter({ value }: { value: number }) {
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

export function EvidenceRow({ evidence }: { evidence: EvidenceSnapshot }) {
  const when = new Date(evidence.event_time);
  return (
    <li className="rounded-md border border-border bg-surface-2 px-3 py-2.5 transition-colors duration-120 hover:bg-surface-3">
      <div className="flex items-baseline justify-between gap-3">
        <span className="text-dense text-foreground">
          {evidence.summary ?? evidence.type}
        </span>
        {/* Mono + tabular: this is a database timestamp, and dates in a column must align. */}
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
        <span className="ml-auto truncate font-mono text-micro text-faint">
          {evidence.id.slice(0, 8)}
        </span>
      </div>
    </li>
  );
}
