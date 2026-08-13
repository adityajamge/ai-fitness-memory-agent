/**
 * "Since your last review" — the newest active insight, gated to whether it is actually new.
 *
 * Renamed from Today's "What changed" in the 2026-08-13 IA revision (DESIGN.md §6.20, §16). The
 * engine still only ever hands back one active insight, so "since last review" is implemented as
 * a client-side gate in `Review.tsx`: the insight renders here only when its `created_at` is
 * newer than the `lastReviewedAt` marker stored in `localStorage` on the previous visit (or
 * always, on a first-ever visit). This component itself is unaware of that marker — it renders
 * whatever `insight` it is handed, or nothing.
 *
 * **It renders only when an insight exists.** A heading over an empty box is the fastest way to
 * make a memory product look like it has no memory, so the absent case returns `null` and the
 * section simply is not there. Review has other things to say, and inventing a placeholder claim
 * to fill a slot is the one thing this product cannot do.
 *
 * Three properties of the engine's model are load-bearing on screen:
 *
 * - `hypothesis` is served **verbatim** from the stored payload. It was composed when the claim
 *   was derived; re-narrating it on read would let the sentence drift from the numbers beneath
 *   it (ADR-12, rule 16).
 * - `pattern_strength` uses the same `ConfidenceMeter` evidence rows use — one 0–1 visual
 *   language, not two (DESIGN.md §0, M8). Never a percentage: ADR-13.12 forbids probability
 *   language, and a meter reads as strength while "82%" reads as odds.
 * - `created_at` is shown as "flagged", separately from the window the claim is *about*. That
 *   bi-temporal split is the product's whole argument — the engine knew before you asked
 *   (ADR-13.10) — and it is invisible unless both clocks are on screen.
 */

import { ArrowUpRight } from "lucide-react";
import type { TodayInsight } from "@/api/schemas";
import { ConfidenceMeter } from "@/components/glassbox/EvidenceRow";

const DAY = { day: "numeric", month: "short" } as const;
const DAY_YEAR = { day: "numeric", month: "short", year: "numeric" } as const;

export interface SinceLastReviewProps {
  insight: TodayInsight | null;
  /** Opens the day the claim's window ends on, in Chat's evidence pane. */
  onOpenDay: (day: string) => void;
}

export function SinceLastReview({ insight, onOpenDay }: SinceLastReviewProps) {
  if (!insight || !insight.hypothesis) return null;

  const windowEnd = insight.window_end ? new Date(insight.window_end) : null;
  const windowStart = insight.window_start ? new Date(insight.window_start) : null;
  const flagged = new Date(insight.created_at);
  // The local calendar day the window closes on — the same YYYY-MM-DD key `GET
  // /api/memories/by-day/{day}` and the timeline strip both use.
  const dayKey = windowEnd ? windowEnd.toISOString().slice(0, 10) : null;

  return (
    <section className="flex flex-col gap-3" aria-labelledby="since-last-review">
      <h2
        id="since-last-review"
        className="font-mono text-micro uppercase tracking-[0.08em] text-faint"
      >
        Since your last review
      </h2>

      <div className="rounded-md border border-border bg-surface p-4">
        <p className="text-body text-foreground">{insight.hypothesis}</p>

        <dl className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1.5 border-t border-border pt-3">
          <div className="flex items-center gap-1.5">
            <dt className="font-mono text-micro uppercase tracking-[0.08em] text-faint">
              strength
            </dt>
            <dd className="flex items-center">
              <ConfidenceMeter value={insight.pattern_strength ?? 0} />
            </dd>
          </div>

          {windowStart && windowEnd && (
            <div className="flex items-center gap-1.5">
              <dt className="font-mono text-micro uppercase tracking-[0.08em] text-faint">
                about
              </dt>
              <dd className="font-mono text-meta tabular-nums text-muted-foreground">
                {windowStart.toLocaleDateString(undefined, DAY)}
                {" – "}
                {windowEnd.toLocaleDateString(undefined, DAY_YEAR)}
              </dd>
            </div>
          )}

          {/* The beat that no competitor's report has: when the engine knew, not when you asked. */}
          <div className="flex items-center gap-1.5">
            <dt className="font-mono text-micro uppercase tracking-[0.08em] text-faint">
              flagged
            </dt>
            <dd className="font-mono text-meta tabular-nums text-muted-foreground">
              {flagged.toLocaleDateString(undefined, DAY_YEAR)}
            </dd>
          </div>

          {insight.evidence_count !== null && (
            <div className="flex items-center gap-1.5">
              <dt className="font-mono text-micro uppercase tracking-[0.08em] text-faint">
                evidence
              </dt>
              <dd className="font-mono text-meta tabular-nums text-muted-foreground">
                {insight.evidence_count}
              </dd>
            </div>
          )}
        </dl>

        {dayKey && (
          <button
            type="button"
            onClick={() => onOpenDay(dayKey)}
            className="mt-3 inline-flex items-center gap-1 rounded-xs text-meta text-signal transition-colors duration-120 hover:text-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-signal"
          >
            Open the evidence
            <ArrowUpRight className="size-3.5" strokeWidth={1.5} aria-hidden="true" />
          </button>
        )}
      </div>
    </section>
  );
}
