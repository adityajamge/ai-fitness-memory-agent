/**
 * The timeline strip — DESIGN.md §6.8, §6.15 ("density bars", one of the two hand-rolled chart
 * types this product has).
 *
 * Permanent, full width, sits directly under the top bar. Rule 7 confines `--signal` to a fixed
 * list that includes "insight caps in the timeline" — nothing else here may use it, which is why
 * a day's bar is `--faint`/`--muted-foreground` and only the 2px insight cap and the `now` marker
 * are signal-colored.
 *
 * Hand-rolled SVG per §12 rule 12 ("no chart library"): a generic chart library fights exact
 * token control, and this needs it for the graph-rule background and the signal cap alone.
 */

import { useEffect, useMemo, useRef, useState, type MouseEvent as ReactMouseEvent } from "react";
import { useTimeline } from "@/api/queries";
import type { TimelineDay } from "@/api/schemas";
import { cn } from "@/lib/utils";

const MS_PER_DAY = 86_400_000;
const BAR_GAP = 0.15;
/** 40px mobile / 48px tablet / 72px desktop rail (§6.8's anatomy). */
const RAIL_HEIGHT = "h-10 sm:h-12 lg:h-[72px]";
/** DESIGN.md §5.8: below 768px, ~340px of scrollable width cannot render one 1px bar per day for
 * a months-long history (unreadable, untappable) — the rail buckets by week instead. */
const MOBILE_QUERY = "(max-width: 767px)";
/** Fixed per-bucket width on mobile, in CSS px — the rail scrolls horizontally rather than
 * squeezing buckets to fit the viewport, so each one stays comfortably tappable. */
const MOBILE_BUCKET_PX = 16;
const DAYS_PER_BUCKET = 7;

function isoDay(date: Date): string {
  return date.toISOString().slice(0, 10);
}

function parseDay(day: string): Date {
  return new Date(`${day}T00:00:00Z`);
}

function todayUtc(): Date {
  const now = new Date();
  return new Date(Date.UTC(now.getFullYear(), now.getMonth(), now.getDate()));
}

/** Every calendar day from the first logged day through today, gaps filled with zero — a
 * continuous rail, not just the days the backend happened to return rows for. */
export function fillRange(days: TimelineDay[]): TimelineDay[] {
  const [first] = days;
  if (!first) return [];
  const start = parseDay(first.day);
  const end = todayUtc();
  const byDay = new Map(days.map((d) => [d.day, d]));
  const filled: TimelineDay[] = [];
  for (let t = start.getTime(); t <= end.getTime(); t += MS_PER_DAY) {
    const iso = isoDay(new Date(t));
    filled.push(byDay.get(iso) ?? { day: iso, n: 0, insights: 0 });
  }
  return filled;
}

/**
 * Chunk the (already-contiguous) daily rail into fixed 7-day buckets, oldest-first, summing
 * counts. Bucketed by array position rather than calendar week: it needs no timezone-aware week
 * math, and because `fillRange` always ends on today, the *last* bucket always ends on today too
 * — which is what keeps the `now` marker meaningfully aligned with the rail's right edge.
 *
 * "keeps changepoint markers at full size as the primary affordance" (§5.8): a week bucket's
 * `insights` count is a sum, so a week containing any insight still gets the signal cap.
 */
function bucketByWeek(days: TimelineDay[]): TimelineDay[] {
  const buckets: TimelineDay[] = [];
  for (let i = 0; i < days.length; i += DAYS_PER_BUCKET) {
    const chunk = days.slice(i, i + DAYS_PER_BUCKET);
    const last = chunk[chunk.length - 1];
    if (!last) continue;
    buckets.push({
      day: last.day,
      n: chunk.reduce((sum, d) => sum + d.n, 0),
      insights: chunk.reduce((sum, d) => sum + d.insights, 0),
    });
  }
  return buckets;
}

const RAIL_BACKGROUND = { backgroundImage: "var(--graph-rule)" };

export interface TimelineProps {
  /** Scrub the conversation to the nearest turn on this date (§9 "click a timeline day"). */
  onScrub?: (day: string) => void;
}

export function Timeline({ onScrub }: TimelineProps) {
  const { data, isPending, isError, refetch } = useTimeline();
  const [hover, setHover] = useState<{ day: TimelineDay; x: number } | null>(null);
  const [isMobile, setIsMobile] = useState(
    () => typeof window !== "undefined" && window.matchMedia(MOBILE_QUERY).matches,
  );
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const mq = window.matchMedia(MOBILE_QUERY);
    const onChange = (e: MediaQueryListEvent) => setIsMobile(e.matches);
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);

  const days = useMemo(() => fillRange(data?.days ?? []), [data]);
  const bars = useMemo(() => (isMobile ? bucketByWeek(days) : days), [days, isMobile]);
  const maxN = useMemo(() => Math.max(1, ...bars.map((d) => d.n)), [bars]);

  if (isPending) {
    return (
      <div
        className={cn("shrink-0 border-b border-border bg-surface", RAIL_HEIGHT)}
        style={RAIL_BACKGROUND}
      />
    );
  }

  if (isError) {
    return (
      <div
        className={cn(
          "flex shrink-0 items-center justify-center gap-2 border-b border-border bg-surface px-4 text-meta text-faint",
          RAIL_HEIGHT,
        )}
      >
        couldn&apos;t load your timeline
        <button
          type="button"
          onClick={() => void refetch()}
          className="text-muted-foreground underline underline-offset-2 hover:text-foreground"
        >
          retry
        </button>
      </div>
    );
  }

  if (days.length === 0) {
    return (
      <div
        className={cn(
          "relative flex shrink-0 items-center justify-center border-b border-border bg-surface",
          RAIL_HEIGHT,
        )}
        style={RAIL_BACKGROUND}
      >
        <span className="font-mono text-meta text-faint">your memory starts here</span>
        <span
          className="absolute right-3 top-1/2 size-1.5 -translate-y-1/2 rounded-full bg-signal"
          aria-hidden="true"
          title="now"
        />
      </div>
    );
  }

  const total = days.reduce((sum, d) => sum + d.n, 0);
  const firstDay = days[0];
  const lastDay = days[days.length - 1];

  // Index math reads the SVG's OWN rect (`event.currentTarget`), not the outer container's —
  // on mobile the SVG is wider than its scrolling viewport, and its rect already reflects the
  // current scroll offset, so this stays correct regardless of how far the rail is scrolled.
  function handleMove(event: ReactMouseEvent<SVGSVGElement>) {
    const svgRect = event.currentTarget.getBoundingClientRect();
    const ratio = (event.clientX - svgRect.left) / svgRect.width;
    const index = Math.min(bars.length - 1, Math.max(0, Math.floor(ratio * bars.length)));
    const day = bars[index];
    // The tooltip is positioned against the OUTER (non-scrolling) container instead, so it
    // reads correctly wherever the mouse currently is in the viewport.
    const outerRect = containerRef.current?.getBoundingClientRect();
    if (day && outerRect) setHover({ day, x: event.clientX - outerRect.left });
  }

  const svgLabel = `Memory timeline: ${total} ${total === 1 ? "memory" : "memories"} across ${days.length} ${days.length === 1 ? "day" : "days"}, from ${firstDay?.day} to ${lastDay?.day}.`;

  return (
    <div
      ref={containerRef}
      className={cn("relative shrink-0 border-b border-border bg-surface", RAIL_HEIGHT)}
      style={RAIL_BACKGROUND}
    >
      {/* The scrolling viewport. Only mobile scrolls; desktop/tablet render at 100% width and
          this div is inert. */}
      <div className={cn("h-full", isMobile && "overflow-x-auto")}>
        {/* The scrolled content. Fixed pixel width on mobile so weekly buckets stay a
            comfortable, consistent size instead of being squeezed to fit (§5.8); 100% on
            desktop/tablet, where the SVG itself stretches via `preserveAspectRatio="none"`. */}
        <div className="relative h-full" style={isMobile ? { width: bars.length * MOBILE_BUCKET_PX } : undefined}>
          <svg
            role="img"
            aria-label={svgLabel}
            viewBox={`0 0 ${bars.length} 100`}
            preserveAspectRatio={isMobile ? undefined : "none"}
            className="h-full w-full"
            onMouseMove={handleMove}
            onMouseLeave={() => setHover(null)}
            onClick={() => hover && onScrub?.(hover.day.day)}
          >
            {bars.map((d, i) => {
              const h = d.n === 0 ? 0 : Math.max(6, (d.n / maxN) * 90);
              return (
                <g key={d.day}>
                  <rect
                    x={i + BAR_GAP / 2}
                    y={100 - h}
                    width={1 - BAR_GAP}
                    height={h}
                    className={hover?.day.day === d.day ? "fill-muted-foreground" : "fill-faint"}
                  />
                  {/* The one other permitted --signal use (rule 7): a changepoint bucket gets a
                      2px cap, full size regardless of bucketing (§5.8). */}
                  {d.insights > 0 && (
                    <rect
                      x={i + BAR_GAP / 2}
                      y={Math.max(0, 100 - h - 3)}
                      width={1 - BAR_GAP}
                      height={2}
                      className="fill-signal"
                    />
                  )}
                </g>
              );
            })}
          </svg>
        </div>
      </div>

      {/* "now" and the hover tooltip are children of the OUTER container, not the scrolling
          one — pinned to the visible viewport regardless of horizontal scroll position, which
          is what "remains horizontally scrollable... with the now marker pinned right" (§5.8)
          means on mobile. */}
      <span
        className="pointer-events-none absolute right-1 top-1 size-1.5 rounded-full bg-signal"
        aria-hidden="true"
        title="now"
      />

      {hover && (
        <div
          className="pointer-events-none absolute bottom-full z-10 mb-1 -translate-x-1/2 whitespace-nowrap rounded-xs border border-border bg-surface-3 px-1.5 py-1 font-mono text-micro text-foreground shadow-popover"
          style={{ left: hover.x }}
        >
          {parseDay(hover.day.day).toLocaleDateString(undefined, {
            month: "short",
            day: "numeric",
            timeZone: "UTC",
          })}
          {" · "}
          {hover.day.n} {hover.day.n === 1 ? "memory" : "memories"}
          {hover.day.insights > 0 && (
            <>
              {" · "}
              {hover.day.insights} {hover.day.insights === 1 ? "insight" : "insights"}
            </>
          )}
        </div>
      )}

      {/* The accessible form of the chart (§6.15): a visually-hidden table of the underlying
          values, alongside the SVG's role="img" summary — not a substitute for it. */}
      <table className="sr-only">
        <caption>Memory density by day</caption>
        <thead>
          <tr>
            <th scope="col">Date</th>
            <th scope="col">Memories</th>
            <th scope="col">Insights</th>
          </tr>
        </thead>
        <tbody>
          {days
            .filter((d) => d.n > 0)
            .map((d) => (
              <tr key={d.day}>
                <td>{d.day}</td>
                <td>{d.n}</td>
                <td>{d.insights}</td>
              </tr>
            ))}
        </tbody>
      </table>
    </div>
  );
}
