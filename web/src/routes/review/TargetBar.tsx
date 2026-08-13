/**
 * One target, one bar — Review's most load-bearing honesty test.
 *
 * There are **four** states here, not two, and collapsing any pair of them is the bug this
 * component exists to prevent:
 *
 * | logged   | target  | renders                                                    |
 * |----------|---------|------------------------------------------------------------|
 * | `null`   | set     | `— / 150 g` + "nothing logged yet". An empty rail, not a 0. |
 * | `0`      | set     | `0 / 150 g`. A real, logged zero. Bar at zero width.        |
 * | number   | `null`  | the number alone, no rail, no percentage.                   |
 * | `null`   | `null`  | the component's own empty state, pointing at Profile.       |
 *
 * A red ring at zero would be the fitness-app reflex; this shows an empty rail and says so in
 * words, because "you have not eaten yet" is information, not a warning.
 *
 * No color encodes progress (§3 rule 4 — `--signal` means *evidence you can open* and nothing
 * else). Fill is `--muted-foreground` on a `--surface-3` rail: the same neutral, meaning-free
 * treatment `ConfidenceMeter` uses, and legible in grayscale.
 */

import type { TodayMetric } from "@/api/schemas";
import { cn } from "@/lib/utils";

export interface TargetBarProps {
  label: string;
  unit: string;
  metric: TodayMetric;
  target: number | null;
  /** Rendered under the bar when a target exists — `compute_targets`' own basis string. */
  basis?: string | null;
  /** True when the user overrode the computed target, so the caption can say which it is. */
  isCustom?: boolean;
}

/** Whole numbers stay whole; a fractional gram keeps one decimal. Never `46.0`. */
const fmt = (n: number): string =>
  Number.isInteger(n) ? String(n) : n.toFixed(1).replace(/\.0$/, "");

export function TargetBar({ label, unit, metric, target, basis, isCustom }: TargetBarProps) {
  // `has_data` rather than a falsy check: `value === 0` is a real logged zero and must not be
  // mistaken for "nothing yet". This is the single line the whole file is about.
  const logged = metric.has_data ? metric.value : null;
  const pct =
    target && target > 0 && logged !== null
      ? Math.min(100, Math.round((logged / target) * 100))
      : 0;
  const isApprox = metric.n_estimated > 0;

  if (target === null && logged === null) {
    return (
      <div className="flex flex-col gap-1.5">
        <span className="text-dense text-muted-foreground">{label}</span>
        <p className="text-meta text-faint">
          No target yet — set your goal in Profile and this fills in.
        </p>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-baseline justify-between gap-3">
        <span className="text-dense text-muted-foreground">{label}</span>
        {/* Mono + tabular: these came out of CockroachDB, and two bars stacked must align on
            the digits (§4.1, §4.2). */}
        <span className="font-mono text-dense tabular-nums text-foreground">
          {logged === null ? <span className="text-faint">—</span> : fmt(logged)}
          {target !== null && <span className="text-faint">{` / ${fmt(target)}`}</span>}
          <span className="text-faint">{` ${unit}`}</span>
        </span>
      </div>

      {target !== null && (
        <div
          className="h-1 w-full overflow-hidden rounded-xs bg-surface-3"
          role="progressbar"
          aria-valuenow={logged ?? 0}
          aria-valuemin={0}
          aria-valuemax={target}
          aria-label={
            logged === null
              ? `${label}: nothing logged yet, target ${fmt(target)} ${unit}`
              : `${label}: ${fmt(logged)} of ${fmt(target)} ${unit}`
          }
        >
          <div
            className={cn(
              "h-full rounded-xs bg-muted-foreground",
              // Width, not transform: the rail is `overflow-hidden` and a scaled child would
              // blur its own edge at sub-pixel widths.
              "transition-[width] duration-medium ease-move",
            )}
            style={{ width: `${pct}%` }}
          />
        </div>
      )}

      <div className="flex items-baseline justify-between gap-3">
        <span className="text-meta text-faint">
          {logged === null
            ? "nothing logged yet"
            : `${metric.n} ${metric.n === 1 ? "entry" : "entries"}${
                isApprox ? " · estimated" : ""
              }`}
        </span>
        {target !== null && logged !== null && (
          <span className="font-mono text-meta tabular-nums text-faint">{pct}%</span>
        )}
      </div>

      {/* The basis is what stops a target being an unexplained number (§6.19). Rendered as a
          `title` rather than always-on text: it is a long sentence, and Review is a briefing. */}
      {target !== null && (basis || isCustom) && (
        <span className="text-meta text-faint" title={basis ?? undefined}>
          {isCustom ? "you set this" : "computed from your profile"}
        </span>
      )}
    </div>
  );
}
