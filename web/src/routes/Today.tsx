/**
 * Today — the home screen (research P0 #1).
 *
 * **A briefing, not a dashboard.** The word matters here as much as it does in `AppScreen`. At
 * 8 AM "today" is empty and AyuMind has no overnight sensor, so the hierarchy every competitor
 * uses — open with what was measured while you slept — is unavailable and would have to be
 * faked. This screen leads with *continuity* instead: how much memory exists, where you stood
 * when we last measured, and one way to change it. That is the one thing a memory product has
 * that a sensor product does not.
 *
 * Order, and nothing may reorder it:
 *
 *   1. state line     — the thesis in one sentence, every figure from the engine
 *   2. targets        — two, never four; empty reads "nothing logged yet", never `0`
 *   3. composer       — the primary action, above the fold at 390px
 *   4. what changed   — the newest active insight, or absent entirely
 *   5. recently logged— receipts, and the way into the day view
 *
 * **This screen does not replace Chat.** The composer hands the message to `/app`, which sends
 * it through the same graph every other turn goes through — Today has no send path of its own,
 * and clicking a memory opens the conversation's existing day view rather than a second diary.
 *
 * **Nothing here is computed client-side.** Every number arrives from `GET /api/today`; this
 * file picks sentence shapes and lays out components. The `steps` disclosure at the bottom is
 * the same `RetrievalQueries` the evidence pane uses — Today asserts figures the user never
 * asked for, so it owes the same "how this was retrieved" affordance a turn does (ADR-12).
 */

import { useState } from "react";
import { Navigate, useNavigate } from "react-router";
import { m, useReducedMotion } from "motion/react";
import { isUnauthorized, useToday } from "@/api/queries";
import { RetrievalQueries } from "@/components/glassbox/RetrievalQueries";
import { Composer } from "@/components/layout/Composer";
import { ThreadSidebarRail } from "@/components/layout/ThreadSidebarRail";
import { TopBar } from "@/components/layout/TopBar";
import { EmptyState } from "@/components/state/EmptyState";
import { ErrorState } from "@/components/state/ErrorState";
import { Timeline } from "@/components/timeline/Timeline";
import { Button } from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/Skeleton";
import { SessionNotice } from "@/session/SessionNotice";
import { useSessionExpired } from "@/session/sessionStore";
import { getActiveThreadId, setActiveThread, startNewThread } from "@/session/threadStore";
import { RecentMemories } from "./today/RecentMemories";
import { StateLine } from "./today/StateLine";
import { TargetBar } from "./today/TargetBar";
import { WhatChanged } from "./today/WhatChanged";

/** The same real, loggable examples `FirstRun` offers — a prompt that produces nothing would
 * discredit the receipt that follows it. Shown only on a genuinely empty account. */
const EXAMPLES = [
  "250g curd, 3 eggs, 200g chicken",
  "ran 5k this morning, felt easy",
  "slept 6h, woke up twice",
] as const;

const DAY_YEAR = { day: "numeric", month: "short", year: "numeric" } as const;

export function Today() {
  const today = useToday();
  const navigate = useNavigate();
  const sessionExpired = useSessionExpired();
  const reduce = useReducedMotion();
  const [draft, setDraft] = useState("");
  // The sidebar highlights whichever thread Chat would resume — Today has no conversation
  // state of its own to read this from, so it's the same localStorage-backed id AppScreen uses.
  const [activeThreadId] = useState(() => getActiveThreadId());

  /** Hand the message to the conversation. Today deliberately has no send path of its own:
   * one turn pipeline, one place receipts and traces are produced. */
  function handleSubmit() {
    const message = draft.trim();
    if (!message) return;
    setDraft("");
    void navigate("/app", { state: { draft: message } });
  }

  /** Clicking a memory or an insight opens that day in the conversation's evidence pane —
   * the day view the timeline strip already owns, never a second diary surface. */
  function openDay(day: string) {
    void navigate("/app", { state: { day } });
  }

  /** The sidebar's "New chat"/thread-select, same hand-off pattern as the composer and day-view
   * clicks above: Today has no thread machinery of its own, so this only ever points the active
   * thread and lets `/app` do the actual work. */
  function handleNewChat() {
    startNewThread();
    void navigate("/app");
  }

  function handleSelectThread(id: string) {
    setActiveThread(id);
    void navigate("/app");
  }

  // 401 before any request has ever succeeded means "not signed in", not "expired".
  if (isUnauthorized(today.error) && !sessionExpired) {
    return <Navigate to="/login" replace />;
  }

  const data = today.data;
  const isEmptyAccount = data?.stats.memories === 0;

  return (
    // `h-dvh` + `overflow-hidden`, matching `AppScreen`'s shell: the top bar and timeline stay
    // put and `main` is the single scroll container. A `min-h-dvh` root with a scrolling child
    // gives two nested scrollers, which on a touchpad means the page and the content fight over
    // the same gesture.
    <div className="flex h-dvh flex-col overflow-hidden bg-background">
      <TopBar />
      <Timeline />

      {/* Row, matching AppScreen's shape (§16 Decisions Log, amended 2026-08-12) — the same
          sidebar rail lives here so account controls and conversation history are consistent
          across both surfaces, not a Chat-only fixture. */}
      <div className="relative flex min-h-0 flex-1 overflow-hidden">
        <ThreadSidebarRail
          activeThreadId={activeThreadId}
          onSelectThread={handleSelectThread}
          onNewChat={handleNewChat}
        />

        <main className="min-h-0 flex-1 overflow-y-auto">
          <div className="mx-auto flex w-full max-w-conversation flex-col gap-8 px-4 py-6 md:px-8 md:py-10">
            {today.isPending ? (
              <TodaySkeleton />
            ) : today.isError && !sessionExpired ? (
              <ErrorState
                size="page"
                title="Couldn't reach your memory"
                detail="The database is unreachable right now."
                preserved="Nothing you logged has been lost."
                onRetry={() => void today.refetch()}
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
                    body="Tell me what you ate, how you trained, how you slept. I'll structure it, remember it, and show you where you stand."
                  >
                    <div className="flex flex-col items-start gap-2 sm:flex-row sm:flex-wrap">
                      {EXAMPLES.map((example) => (
                        <Button
                          key={example}
                          variant="secondary"
                          size="md"
                          onClick={() => setDraft(example)}
                          className="max-w-full justify-start overflow-hidden text-ellipsis"
                        >
                          {example}
                        </Button>
                      ))}
                    </div>
                  </EmptyState>
                ) : (
                  <>
                    <StateLine today={data} />

                    {/* Two targets. Stacked below `sm` so the numbers never compress; side by
                        side above it, where 860px of column comfortably carries both. */}
                    <section className="flex flex-col gap-5 sm:flex-row sm:gap-10" aria-label="Today's targets">
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
                  </>
                )}

                {/* The primary action, in flow rather than pinned: at 390x844 everything above it
                    measures ~450px, so it is above the fold without a sticky bar covering the
                    content the user came to read. */}
                <div className="flex flex-col gap-2">
                  {sessionExpired && <SessionNotice />}
                  <Composer
                    value={draft}
                    onChange={setDraft}
                    onSubmit={handleSubmit}
                    isLocked={sessionExpired}
                  />
                  <p className="text-meta text-faint">
                    Sends to your conversation, where the engine shows its work.
                  </p>
                </div>

                {!isEmptyAccount && (
                  <>
                    <WhatChanged insight={data.insight} onOpenDay={openDay} />
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
    </div>
  );
}

/** The loading state, shaped like the content it replaces — a spinner here would flash and
 * reflow; matching blocks let the page settle in place (§6.10, rule 19). */
function TodaySkeleton() {
  return (
    <div className="flex flex-col gap-8">
      <div className="flex flex-col gap-2">
        <Skeleton className="h-3.5 w-56" />
        <Skeleton className="h-6 w-full max-w-[46ch]" />
        <Skeleton className="h-3 w-40" />
      </div>
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
