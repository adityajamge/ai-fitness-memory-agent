/**
 * Review — the memory briefing (DESIGN.md §6.20). Renamed and rebuilt from Today by the
 * 2026-08-13 IA revision (§16 Decisions Log).
 *
 * **Not a dashboard, and not a briefing that doubles as home.** Chat is home now (§6.13); Review
 * answers a different question, on its own schedule: "What is happening in my health memory, and
 * what has changed?" Full-width, single column, no sidebar — conversation history is scoped to
 * Chat only (§6.21).
 *
 * Order, and nothing may invert it:
 *
 *   1. state line          — the thesis in one sentence, plus the memory-system counts formerly
 *                             shown in the global top bar
 *   2. memory-density timeline — formerly global above every screen; now here, where density has
 *                             context (targets, insights, coverage) instead of sitting above
 *                             every screen regardless of relevance
 *   3. since your last review — the newest active insight, gated against a client-side
 *                             `lastReviewedAt` marker (§16, 2026-08-13: no new backend field)
 *   4. targets              — two, never four; empty reads "nothing logged yet", never `0`
 *   5. recently logged      — receipts, and the way into the day view
 *
 * **Review has no composer and no send path.** Today's composer existed because Today was the
 * landing page and needed a fast way to send a message without navigating away. Chat is the
 * landing page now, so that shortcut and its `location.state.draft` hand-off are gone. The
 * day-view hand-off stays: clicking the timeline, an insight, or a memory row still opens that
 * day in Chat's evidence pane via `navigate("/app", { state: { day } })` — the same mechanism
 * Today used, and `AppScreen`'s handling of it is unchanged.
 */

import { useEffect, useRef, useState } from "react";
import { Navigate, useNavigate } from "react-router";
import { m, useReducedMotion } from "motion/react";
import { isUnauthorized, useReview } from "@/api/queries";
import { RetrievalQueries } from "@/components/glassbox/RetrievalQueries";
import { TopBar } from "@/components/layout/TopBar";
import { EmptyState } from "@/components/state/EmptyState";
import { ErrorState } from "@/components/state/ErrorState";
import { Timeline } from "@/components/timeline/Timeline";
import { Button } from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/Skeleton";
import { RecentMemories } from "./review/RecentMemories";
import { SinceLastReview } from "./review/SinceLastReview";
import { StateLine } from "./review/StateLine";
import { TargetBar } from "./review/TargetBar";

const DAY_YEAR = { day: "numeric", month: "short", year: "numeric" } as const;

/** `localStorage` marker for "since your last review" (§16, 2026-08-13) — a deliberate,
 * documented trade for this revision: no cross-device sync, resets if storage is cleared.
 * Acceptable for a single-session demo; revisit with a real `last_reviewed_at` column only if
 * cross-device drift becomes an actual reported problem, not preemptively. */
const LAST_REVIEWED_KEY = "ayumind:lastReviewedAt";

export function Review() {
  const review = useReview();
  const navigate = useNavigate();
  const reduce = useReducedMotion();

  // Captured once, before the mount effect below overwrites it — the marker as it stood on the
  // PREVIOUS visit, which is exactly what "since" needs to compare against. Reading localStorage
  // fresh on every render would immediately reflect this visit's own update and always read
  // "not new" after the first paint.
  const [lastReviewedAt] = useState<string | null>(() =>
    typeof window === "undefined" ? null : localStorage.getItem(LAST_REVIEWED_KEY),
  );
  // Guards the marker write below so it fires exactly once per mount, the first time real data
  // is on screen — not on every background refetch `useReview`'s `staleTime: 0` can trigger
  // (same `seeded.current` pattern AppScreen/ProfileSettings use for their own once-per-mount
  // effects).
  const marked = useRef(false);

  /** Opens a day (a memory, an insight, or a timeline bar) in Chat's evidence pane — Review owns
   * no day-view renderer of its own (§6.20's "no machinery" rule, carried over from Today). */
  function openDay(day: string) {
    void navigate("/app", { state: { day } });
  }

  // 401 before any request has ever succeeded means "not signed in", not "expired".
  if (isUnauthorized(review.error)) {
    return <Navigate to="/login" replace />;
  }

  const data = review.data;
  const isEmptyAccount = data?.stats.memories === 0;

  const insight = data?.insight ?? null;
  const isInsightNew =
    Boolean(insight) && (!lastReviewedAt || new Date(insight!.created_at) > new Date(lastReviewedAt));

  // Marks "reviewed" the first time real, non-empty content actually renders — an empty account
  // has nothing to mark as seen, and marking on every render would make `lastReviewedAt` chase
  // itself instead of anchoring to the previous visit.
  useEffect(() => {
    if (marked.current || !data || isEmptyAccount) return;
    marked.current = true;
    localStorage.setItem(LAST_REVIEWED_KEY, new Date().toISOString());
  }, [data, isEmptyAccount]);

  return (
    // `h-dvh` + `overflow-hidden`, matching AppScreen's shell: the top bar stays put and `main`
    // is the single scroll container.
    <div className="flex h-dvh flex-col overflow-hidden bg-background">
      <TopBar />

      {/* No sidebar row here (§6.13/§6.21) — Review is full-width at every breakpoint. */}
      <main className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto flex w-full max-w-conversation flex-col gap-8 px-4 py-6 md:px-8 md:py-10">
          {review.isPending ? (
            <ReviewSkeleton />
          ) : review.isError ? (
            <ErrorState
              size="page"
              title="Couldn't reach your memory"
              detail="The database is unreachable right now."
              preserved="Nothing you logged has been lost."
              onRetry={() => void review.refetch()}
            />
          ) : data ? (
            <m.div
              initial={reduce ? false : { opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: reduce ? 0 : 0.32, ease: [0.2, 0, 0, 1] }}
              className="flex flex-col gap-8"
            >
              {isEmptyAccount ? (
                <EmptyState
                  title="Your memory starts here."
                  body="There's nothing to review yet. Head to Chat and tell me what you ate, how you trained, how you slept — Review fills in once there's something to show."
                >
                  <Button variant="secondary" size="md" onClick={() => void navigate("/app")}>
                    Go to Chat
                  </Button>
                </EmptyState>
              ) : (
                <>
                  <StateLine today={data} />

                  <div>
                    <Timeline onScrub={openDay} />
                  </div>

                  <SinceLastReview
                    insight={isInsightNew ? insight : null}
                    onOpenDay={openDay}
                  />

                  {/* Two targets. Stacked below `sm` so the numbers never compress; side by
                      side above it, where 860px of column comfortably carries both. */}
                  <section
                    className="flex flex-col gap-5 sm:flex-row sm:gap-10"
                    aria-label="Current targets"
                  >
                    <div className="flex-1">
                      <TargetBar
                        label="Protein"
                        unit="g"
                        metric={data.today.protein_g}
                        target={data.targets.protein_g}
                        basis={data.targets.basis}
                        isCustom={data.targets.are_custom}
                      />
                    </div>
                    <div className="flex-1">
                      <TargetBar
                        label="Energy"
                        unit="kcal"
                        metric={data.today.kcal}
                        target={data.targets.calorie_kcal}
                        basis={data.targets.basis}
                        isCustom={data.targets.are_custom}
                      />
                    </div>
                  </section>

                  <RecentMemories memories={data.recent} onOpenDay={openDay} />

                  {/* Weight is a line, not a card (research §06): one number and when it was
                      measured is the whole useful content, and a card would imply a surface
                      that does not exist yet. */}
                  {data.latest_weight && (
                    <p className="text-meta text-faint">
                      Last weight{" "}
                      <span className="font-mono tabular-nums text-muted-foreground">
                        {data.latest_weight.weight_kg} kg
                      </span>{" "}
                      on{" "}
                      <span className="font-mono tabular-nums text-muted-foreground">
                        {new Date(data.latest_weight.event_time).toLocaleDateString(
                          undefined,
                          DAY_YEAR,
                        )}
                      </span>
                    </p>
                  )}

                  <RetrievalQueries steps={data.steps} />
                </>
              )}
            </m.div>
          ) : null}
        </div>
      </main>
    </div>
  );
}

/** The loading state, shaped like the content it replaces — a spinner here would flash and
 * reflow; matching blocks let the page settle in place (§6.10, rule 19). */
function ReviewSkeleton() {
  return (
    <div className="flex flex-col gap-8">
      <div className="flex flex-col gap-2">
        <Skeleton className="h-3.5 w-56" />
        <Skeleton className="h-6 w-full max-w-[46ch]" />
        <Skeleton className="h-3 w-40" />
      </div>
      <Skeleton className="h-16 w-full" />
      <div className="flex flex-col gap-5 sm:flex-row sm:gap-10">
        <div className="flex flex-1 flex-col gap-2">
          <Skeleton className="h-3.5 w-full" />
          <Skeleton className="h-1 w-full" />
        </div>
        <div className="flex flex-1 flex-col gap-2">
          <Skeleton className="h-3.5 w-full" />
          <Skeleton className="h-1 w-full" />
        </div>
      </div>
      <Skeleton className="h-12 w-full" />
    </div>
  );
}
