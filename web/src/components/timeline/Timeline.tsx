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

import {
  memo,
  useEffect,
  useMemo,
  useRef,
  useState,
  type MouseEvent as ReactMouseEvent,
  type WheelEvent as ReactWheelEvent,
} from "react";
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
const DAYS_PER_BUCKET = 7;
/** The rail defaults to showing roughly this many days, scrolled to "now" at the right edge —
 * older history is reached by scrolling left, rather than every day of a long account's real
 * history being squeezed into one sliver at the far right (reported live: a several-month
 * account rendered as a wall of empty grid with all the actual bars crammed into ~2% of the
 * width). Bar width is derived from this and the rail's own measured width, so it holds at any
 * viewport size instead of being tuned to one. */
const VISIBLE_DAYS = 90;
/** Never let a bar get thinner than this, even on a narrow viewport — the floor for both
 * legibility and (on mobile) a tappable target. */
const MIN_BAR_PX = 3;

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

// Memoized: `onScrub` is a stable setState function passed from `AppScreen`, so this rail (up to
// 90 SVG bars, remeasured and re-bucketed) has no reason to re-render on a composer keystroke.
export const Timeline = memo(function Timeline({ onScrub }: TimelineProps) {
  const { data, isPending, isError, refetch } = useTimeline();
  const [hover, setHover] = useState<{ day: TimelineDay; x: number } | null>(null);
  const [isMobile, setIsMobile] = useState(
    () => typeof window !== "undefined" && window.matchMedia(MOBILE_QUERY).matches,
  );
  const [railWidth, setRailWidth] = useState(0);
  const containerRef = useRef<HTMLDivElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  // Hover perf: see `handleMove` below. `outerRect` is cached here rather than re-measured on
  // every mousemove — it only actually changes on resize, so the ResizeObserver effect refreshes
  // it alongside `railWidth`.
  const outerRectRef = useRef<DOMRect | null>(null);
  const pendingMoveRef = useRef<{ clientX: number; svg: SVGSVGElement } | null>(null);
  const rafIdRef = useRef<number | null>(null);

  useEffect(() => {
    const mq = window.matchMedia(MOBILE_QUERY);
    const onChange = (e: MediaQueryListEvent) => setIsMobile(e.matches);
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);

  // Measures the rail itself, not the window — a chart embedded in a narrower column still gets
  // bars sized off the space it actually has.
  //
  // `[isPending]`, not `[]`: `containerRef` is only attached in the loaded branch below, not the
  // skeleton one this component renders first (data is a real network fetch, so `isPending` is
  // true on mount every time). An empty-deps effect runs once, sees `containerRef.current` still
  // null, and gives up forever — `railWidth` stays 0, `contentWidth` falls back to `"100%"`, and
  // the entire history gets squeezed to fit instead of capping at ~90 days with the rest a scroll
  // away. Depending on `isPending` re-fires this effect exactly when the skeleton gives way to
  // the real container, by which point the ref is populated.
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const ro = new ResizeObserver(([entry]) => {
      if (entry) setRailWidth(entry.contentRect.width);
      // Refreshed here, not on every mousemove — see `handleMove`.
      outerRectRef.current = el.getBoundingClientRect();
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [isPending]);

  // The rAF loop this drives is per-component, not per-mount-forever, so it needs to stop when
  // the component unmounts mid-frame (fast route change while hovering) rather than firing into
  // a detached tree.
  useEffect(() => {
    return () => {
      if (rafIdRef.current != null) cancelAnimationFrame(rafIdRef.current);
    };
  }, []);

  const days = useMemo(() => fillRange(data?.days ?? []), [data]);
  const bars = useMemo(() => (isMobile ? bucketByWeek(days) : days), [days, isMobile]);
  const maxN = useMemo(() => Math.max(1, ...bars.map((d) => d.n)), [bars]);

  // One bar = one day on desktop, one bucket = seven days on mobile — so "90 visible days" is
  // 90 bars on one and ~13 on the other, and both land on the same real span of history.
  const visibleBars = isMobile ? Math.ceil(VISIBLE_DAYS / DAYS_PER_BUCKET) : VISIBLE_DAYS;
  const barPx = railWidth > 0 ? Math.max(railWidth / visibleBars, MIN_BAR_PX) : 0;
  // Bar width is always `barPx` regardless of history length — a 3-day-old account's bars are
  // the same width as they'd be in a 90-day rail, not stretched to fill it (reported live: a
  // brand-new account's few bars rendered enormous, stretched across the full rail width). A
  // short history just leaves empty rule-background to the left, right-anchored (see the
  // `justify-end` below) so the bars still sit next to the pinned `now` marker.
  const contentWidth = barPx > 0 ? barPx * bars.length : undefined;

  // Lands on "now" by default — the most recent ~90 days are what's visible without scrolling;
  // everything older is one scroll away, never rendered as a barely-visible sliver.
  useEffect(() => {
    scrollRef.current?.scrollTo({ left: scrollRef.current.scrollWidth, behavior: "auto" });
  }, [contentWidth, bars.length]);

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

  /**
   * Rate-limited to one update per animation frame — plain per-event handling measurably felt
   * late (reported live): raw `mousemove` can fire far faster than the screen repaints, and each
   * call was doing two synchronous `getBoundingClientRect()` reads (forced layout) plus a
   * `setState`, so fast mouse movement queued up more work than the browser could keep up with,
   * and the highlight visibly trailed the cursor.
   *
   * The event itself is cheap to record (a ref write, no measurement); only the rAF callback that
   * actually runs the math is throttled, and it always reads the LATEST recorded position rather
   * than whatever position was current when the frame was scheduled — otherwise fast movement
   * would track one frame behind instead of just missing the frames in between.
   */
  function handleMove(event: ReactMouseEvent<SVGSVGElement>) {
    pendingMoveRef.current = { clientX: event.clientX, svg: event.currentTarget };
    if (rafIdRef.current != null) return;
    rafIdRef.current = requestAnimationFrame(() => {
      rafIdRef.current = null;
      const pending = pendingMoveRef.current;
      const outerRect = outerRectRef.current;
      if (!pending || !outerRect) return;
      // The SVG's OWN rect (not cached — it moves under the cursor as the rail scrolls
      // horizontally) is what index math needs; the OUTER, non-scrolling container's cached
      // rect is what the tooltip's position needs, so it reads correctly wherever the mouse
      // currently is in the viewport rather than scrolling away with the bars.
      const svgRect = pending.svg.getBoundingClientRect();
      const ratio = (pending.clientX - svgRect.left) / svgRect.width;
      const index = Math.min(bars.length - 1, Math.max(0, Math.floor(ratio * bars.length)));
      const day = bars[index];
      if (day) setHover({ day, x: pending.clientX - outerRect.left });
    });
  }

  function handleLeave() {
    if (rafIdRef.current != null) {
      cancelAnimationFrame(rafIdRef.current);
      rafIdRef.current = null;
    }
    pendingMoveRef.current = null;
    setHover(null);
  }

  // A plain vertical mouse wheel does nothing on an `overflow-x-auto` element by default —
  // only shift+wheel or a trackpad's horizontal axis does. The rail has no vertical scroll of
  // its own, so redirect ordinary wheel input into horizontal movement instead of leaving it
  // dead. Skipped when the gesture is already horizontal (trackpad / shift+wheel) so that isn't
  // double-applied.
  function handleWheel(event: ReactWheelEvent<HTMLDivElement>) {
    const el = scrollRef.current;
    if (!el || Math.abs(event.deltaX) >= Math.abs(event.deltaY)) return;
    event.preventDefault();
    el.scrollLeft += event.deltaY;
  }

  const svgLabel = `Memory timeline: ${total} ${total === 1 ? "memory" : "memories"} across ${days.length} ${days.length === 1 ? "day" : "days"}, from ${firstDay?.day} to ${lastDay?.day}. Showing the most recent ~${VISIBLE_DAYS} days by default — scroll left for earlier history.`;

  return (
    <div
      ref={containerRef}
      className={cn("relative shrink-0 border-b border-border bg-surface", RAIL_HEIGHT)}
      style={RAIL_BACKGROUND}
    >
      {/* The scrolling viewport — every breakpoint scrolls now, landed on "now" by default
          (the effect above) rather than stretching the whole history to fit and rendering a
          long account as a wall of empty grid with all its real bars in a 2%-wide sliver. */}
      <div
        ref={scrollRef}
        onWheel={handleWheel}
        className="scrollbar-none flex h-full justify-end overflow-x-auto"
      >
        {/* `barPx * bars.length` (see `contentWidth`) — bar width is derived from the rail's
            measured width, not a hardcoded constant, so it holds at any viewport size.
            `shrink-0`: the flex parent's `justify-end` would otherwise squeeze this below its
            explicit width once content overflows, which breaks the scroll. */}
        <div className="relative h-full shrink-0" style={{ width: contentWidth ?? "100%" }}>
          <svg
            role="img"
            aria-label={svgLabel}
            viewBox={`0 0 ${bars.length} 100`}
            preserveAspectRatio="none"
            className="h-full w-full"
            onMouseMove={handleMove}
            onMouseLeave={handleLeave}
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
          values, alongside the SVG's role="img" summary — not a substitute for it.

          `sr-only` goes on a **wrapper div, never on the <table> itself**. The utility hides an
          element by pinning it to 1x1 and clipping it, and a `<table>` refuses to be 1x1: it
          sizes to its content regardless. The result was invisible but not weightless — a
          114-day account produced a clipped, absolutely-positioned 2,784px box that pushed
          `document.scrollHeight` to nearly three screens of empty scroll below the page. It
          went unnoticed while the only consumer was `AppScreen`, whose shell is
          `h-dvh overflow-hidden` and clipped it by accident; the Today screen scrolls, so it
          surfaced there immediately. */}
      <div className="sr-only">
      <table>
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
    </div>
  );
});
