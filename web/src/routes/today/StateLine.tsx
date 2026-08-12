/**
 * The state line — the first thing on Today, and the product's thesis in one sentence.
 *
 * Two rules shape it:
 *
 * 1. **Every number comes from `GET /api/today`.** Nothing is computed here. The component
 *    chooses a *sentence shape* from which facts exist and drops the engine's figures into it;
 *    it never derives a figure, and there is no arithmetic in this file beyond one subtraction
 *    that the caption below spells out (`target − logged`, both server-supplied).
 * 2. **It is not a greeting.** DESIGN.md §2's voice table rejects "Great job crushing your
 *    protein goals!" by name. This reads like a lab report opening: what we have, and where you
 *    stood last time we measured.
 *
 * Sans is the app talking; mono is the database talking (§4.1). The split runs *inside* the
 * sentence, which is the clearest possible demonstration of the rule — "Yesterday:" is sans,
 * "138 g" is mono, and a reader learns the convention without being told it.
 */

import type { TodayResponse } from "@/api/schemas";

/** Whole numbers stay whole; a fractional gram keeps one decimal. */
const fmt = (n: number): string =>
  Number.isInteger(n) ? String(n) : n.toFixed(1).replace(/\.0$/, "");

function Figure({ children }: { children: React.ReactNode }) {
  return <span className="font-mono tabular-nums text-foreground">{children}</span>;
}

export function StateLine({ today }: { today: TodayResponse }) {
  const { stats, yesterday, targets, days_logged_last_7: covered } = today;
  const yProtein = yesterday.protein_g;
  const proteinTarget = targets.protein_g;

  // `has_data`, never a falsy check on `value` — a logged zero is data (see TargetBar).
  const hasYesterday = yProtein.has_data && yProtein.value !== null;
  const delta =
    hasYesterday && proteinTarget !== null ? (yProtein.value as number) - proteinTarget : null;

  return (
    <div className="flex flex-col gap-2">
      <p className="text-lead text-muted-foreground">
        <Figure>{stats.days}</Figure>
        {stats.days === 1 ? " day of memory · " : " days of memory · "}
        <Figure>{stats.memories}</Figure>
        {stats.memories === 1 ? " memory" : " memories"}
        {stats.insights > 0 && (
          <>
            {" · "}
            <Figure>{stats.insights}</Figure>
            {stats.insights === 1 ? " insight" : " insights"}
          </>
        )}
      </p>

      <p className="text-h3 text-foreground">
        {hasYesterday ? (
          <>
            Yesterday: <Figure>{fmt(yProtein.value as number)} g</Figure> protein
            {delta !== null && (
              <span className="text-muted-foreground">
                {delta >= 0 ? " — " : " — "}
                <Figure>{fmt(Math.abs(delta))} g</Figure>
                {delta >= 0 ? " above " : " below "}
                your <Figure>{fmt(proteinTarget as number)} g</Figure> target.
              </span>
            )}
            {delta === null && <span className="text-muted-foreground">.</span>}
          </>
        ) : (
          <span className="text-muted-foreground">Nothing logged yesterday.</span>
        )}
      </p>

      {/* Coverage, not a streak (§10 of the research): this is the same quantity that gates
          `analytics.pattern_strength`, stated as what it is — how much of the last week the
          numbers above actually rest on. No flame, no reward, nothing to protect. */}
      <p className="text-meta text-faint">
        <Figure>{covered}</Figure>
        {` of the last 7 days logged`}
        {covered < 4 && " — averages get more trustworthy as this rises"}
      </p>
    </div>
  );
}
