/**
 * Skeleton — DESIGN.md §6.10, loading kind 1.
 *
 * For content whose shape is known: evidence rows, turn history, stats. A block at the exact
 * final dimensions, so nothing reflows when the data lands.
 *
 * The shimmer is CSS-only and disabled by the global `prefers-reduced-motion` block in
 * theme.css, which leaves a static `--surface-2` block. That is the correct reduced state: the
 * placeholder still communicates "content of this shape is coming", it just stops moving.
 */

import { cn } from "@/lib/utils";

export function Skeleton({ className }: { className?: string }) {
  return (
    <div
      className={cn(
        "animate-pulse rounded-sm bg-surface-2",
        // 1.4s rather than Tailwind's default 2s: at 2s the pane reads as stalled rather than
        // loading, and this API's p50 is well under a second.
        "[animation-duration:1.4s]",
        className,
      )}
      // Decorative. The live region announcing "loading" belongs to the container that knows
      // *what* is loading; a skeleton announcing itself would produce a burst of noise.
      aria-hidden="true"
    />
  );
}
