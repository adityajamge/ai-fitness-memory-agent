/**
 * The product — DESIGN.md §9.
 *
 * Conversation-first with the engine continuously visible. The word "dashboard" is banned here on
 * purpose: it invites the wireframe-v2 mistake of demoting the conversation, which was rejected
 * after two drafts.
 *
 * Information hierarchy, and nothing may invert it: **the answer, the evidence, the history, the
 * system.** A stats widget that visually outweighs the conversation is a bug.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { Navigate, useLocation, useNavigate } from "react-router";
import { PanelRightClose, PanelRightOpen } from "lucide-react";
import { useReducedMotion } from "motion/react";
import { ApiError } from "@/api/client";
import { StreamUnavailableError, streamChat } from "@/api/chatStream";
import {
  isUnauthorized,
  useInvalidateAfterTurn,
  useMemoriesByDay,
  useSendMessage,
  useStats,
  useTurns,
} from "@/api/queries";
import type { ChatResponse } from "@/api/schemas";
import { FirstAskHint, FirstRun } from "@/components/FirstRun";
import { EvidenceDrawer } from "@/components/glassbox/EvidenceDrawer";
import { EvidencePane } from "@/components/glassbox/EvidencePane";
import { useSelectedTrace } from "@/components/glassbox/useSelectedTrace";
import { useTurnEvidence } from "@/components/glassbox/useTurnEvidence";
import { Composer } from "@/components/layout/Composer";
import { Conversation } from "@/components/layout/Conversation";
import { ThreadSidebarRail } from "@/components/layout/ThreadSidebarRail";
import { TopBar } from "@/components/layout/TopBar";
import { ErrorState } from "@/components/state/ErrorState";
import { Button } from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/Skeleton";
import { SessionNotice } from "@/session/SessionNotice";
import { useSessionExpired } from "@/session/sessionStore";
import { getActiveThreadId, setActiveThread, startNewThread } from "@/session/threadStore";
import type { ChatTurn } from "@/types/turn";
import { cn } from "@/lib/utils";

let localId = 0;
const nextId = () => `local-${++localId}`;

/** Matches the `lg` breakpoint where the evidence pane becomes a column instead of a drawer. */
const DESKTOP_QUERY = "(min-width: 1024px)";

export function AppScreen() {
  const location = useLocation();
  const navigate = useNavigate();
  const stats = useStats();
  const send = useSendMessage();
  const invalidateAfterTurn = useInvalidateAfterTurn();
  const sessionExpired = useSessionExpired();
  const reduce = useReducedMotion();
  // Cancels a superseded turn's SSE connection — a retry or a fast second message must not let
  // a stale stream keep writing into a `pending` slot that has since moved on.
  const streamAbortRef = useRef<AbortController | null>(null);

  const [draft, setDraft] = useState("");
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [selectedTurnId, setSelectedTurnId] = useState<string | null>(null);
  // Always defined from the first render — persisted per `session/threadStore.ts`, not lazily
  // minted on the first sent message. "New chat" is what changes it.
  const [threadId, setThreadId] = useState<string>(() => getActiveThreadId());
  const history = useTurns(threadId);
  const [showAskHint, setShowAskHint] = useState(false);
  const [activeCitation, setActiveCitation] = useState<string | null>(null);
  const [isDrawerOpen, setDrawerOpen] = useState(false);
  const [isDesktop, setDesktop] = useState(
    () => typeof window !== "undefined" && window.matchMedia(DESKTOP_QUERY).matches,
  );
  // Set by a timeline click, consumed once by Conversation's scroll-and-highlight effect (§9
  // "click a timeline day"), then cleared here so clicking the same day twice still re-triggers.
  const [scrubDay, setScrubDay] = useState<string | null>(null);
  // The OTHER thing a timeline click does: puts the engine pane into "day view" — every memory
  // logged that day, not a turn's retrieval trace (a bar has no query behind it). Persists past
  // the one-shot `scrubDay` above until the user sends a new turn, clicks a citation, or exits
  // explicitly; unlike `scrubDay` there is nothing to "consume once", since the pane just renders
  // whatever this is set to.
  const [dayView, setDayView] = useState<string | null>(null);
  const dayMemories = useMemoriesByDay(dayView);
  // The evidence pane is "FIXED and never flexing" (§5.7) — collapsing it to 0 on request is a
  // deliberate escape hatch, not a contradiction: nothing stretches to fill the reclaimed space
  // automatically, the conversation column just gets more margin either side of its own cap.
  const [isPaneCollapsed, setPaneCollapsed] = useState(false);
  const seeded = useRef(false);

  // The pane follows the selected turn; when nothing is selected it follows the newest answer,
  // which is what "following conversation" means.
  const selectedTurn =
    turns.find((t) => t.id === selectedTurnId) ??
    [...turns].reverse().find((t) => t.kind === "assistant") ??
    null;
  const { trace, isLoading: isTraceLoading } = useSelectedTrace(selectedTurn);

  // One batch request per selected turn, shared by the chips and the pane (T16).
  const { rows, missing, isHydrating } = useTurnEvidence(trace);

  useEffect(() => {
    const mq = window.matchMedia(DESKTOP_QUERY);
    const onChange = (e: MediaQueryListEvent) => setDesktop(e.matches);
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);

  // Seed from persisted history exactly once. After that the local list is authoritative: it
  // carries receipts, which `GET /api/turns` does not return, and re-seeding would drop them.
  //
  // **History is PREPENDED, never assigned.** `GET /api/turns` can resolve after the user has
  // already sent — Playwright hits this every run, and a fast typist hits it for real — and a
  // plain `setTurns(history)` silently erased the in-flight turn. The symptom was vicious: the
  // memory was written and the stats ticked, so everything looked fine except that the answer
  // never appeared.
  useEffect(() => {
    if (seeded.current || !history.data) return;
    seeded.current = true;
    const seededTurns: ChatTurn[] = history.data.turns.map((t) =>
        t.role === "user"
          ? { kind: "user" as const, id: t.id, content: t.content ?? "", createdAt: t.created_at }
          : {
              kind: "assistant" as const,
              id: t.id,
              content: t.content ?? "",
              receipts: [],
              citations: [],
              citationReport: null,
              // History carries no inline trace; `turnId` is how it gets fetched on demand.
              trace: null,
              turnId: t.has_trace ? t.id : null,
              errors: [],
              createdAt: t.created_at,
            },
    );
    setTurns((prev) => [...seededTurns, ...prev]);
  }, [history.data]);

  // A superseded stream (component unmount, or the user fires a second turn before the first
  // resolves) must not keep writing into state after the fact.
  useEffect(() => () => streamAbortRef.current?.abort(), []);

  // True regardless of which transport is carrying the turn — `send.isPending` alone would go
  // dark during the SSE path, since that transport never touches the mutation.
  const isSending = turns.some((t) => t.kind === "pending");

  // The keyboard shortcut §9 lists that is still worth having without the command palette it was
  // specified alongside (§13: cut-eligible, ranked below everything else in the Phase-6 priority
  // list). `/` (focus composer) and `⌘Enter` (send) already live in Composer; this covers the
  // one that is page-level. `Esc` blurring the active element is the browser's native behavior
  // for most controls, so there is nothing to add for it here. `T` (focus timeline) is gone: the
  // timeline no longer renders inside Chat (2026-08-13 IA revision — it lives in Review, §6.20).
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null;
      const typing =
        target?.tagName === "INPUT" || target?.tagName === "TEXTAREA" || target?.isContentEditable;
      if (typing) return;
      if (event.key === "e" || event.key === "E") {
        if (!isDesktop) setDrawerOpen((v) => !v);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [isDesktop]);

  /**
   * The signature interaction (§5.6). On mobile the same gesture opens the drawer, so the
   * claim→proof link survives the smallest screen — that coupling is deliberate, not incidental.
   */
  const activateCitation = useCallback(
    (id: string, turnId: string) => {
      // Selecting the turn as well is what makes a *history* citation work: the pane switches to
      // that turn's trace, fetching it if needed, instead of resolving the chip against whatever
      // happens to be loaded.
      setSelectedTurnId(turnId);
      setActiveCitation((current) => (current === id ? null : id));
      // A citation is more specific than "show me this whole day" — exits day view rather than
      // leaving the pane torn between two things it could be showing.
      setDayView(null);
      if (!isDesktop) setDrawerOpen(true);
    },
    [isDesktop],
  );

  // A stable identity, same reason as `sendTurn` above: `Conversation` is memoized, and an inline
  // arrow here would recreate `onScrubHandled` (and force a re-render) on every keystroke.
  const clearScrubDay = useCallback(() => setScrubDay(null), []);

  // Scrolls the conversation to a given day (`scrubDay`, handled by `Conversation`) AND puts the
  // engine pane into day view — every memory logged that day, not whichever turn's trace
  // happened to be selected before. The two are independent (a day with no matching turn in the
  // loaded conversation still gets a day view), which is why this sets both rather than routing
  // one through the other. Reached two ways: the `location.state.day` hand-off below (Review's
  // timeline, an insight, or a memory row — §6.20) is the only path since the 2026-08-13 IA
  // revision moved the timeline itself out of Chat; the name is unchanged from when a timeline
  // bar inside Chat called this directly.
  const handleScrub = useCallback(
    (day: string) => {
      setScrubDay(day);
      setDayView(day);
      if (!isDesktop) setDrawerOpen(true);
    },
    [isDesktop],
  );

  const exitDayView = useCallback(() => setDayView(null), []);

  // 401 before any request has ever succeeded means "not signed in", not "expired" — route to
  // login rather than raising a notice over an app the user cannot see anyway.
  if (isUnauthorized(stats.error) && !sessionExpired) {
    return <Navigate to="/login" replace />;
  }

  const isEmptyAccount = stats.data?.memories === 0 && turns.length === 0;
  const paneProps = {
    trace,
    rows,
    isHydrating: isHydrating || isTraceLoading,
    missingCount: missing.size,
    hasTurns: turns.length > 0,
    activeId: activeCitation,
    dayView: dayView
      ? {
          day: dayView,
          memories: dayMemories.data?.memories ?? [],
          isLoading: dayMemories.isPending,
          onExit: exitDayView,
        }
      : null,
  };

  /** `replaceId` is set when retrying: the failed turn becomes the new pending one in place,
   * so the conversation does not grow a duplicate every time a retry succeeds.
   *
   * **Transport: SSE first, plain `POST /api/chat` as the automatic fallback (M6).** DESIGN.md
   * §11 flags SSE through Express Mode's shared ALB as unproven, so this does not pick one
   * transport statically — it tries the stream, and any failure to *establish or complete* it
   * (`StreamUnavailableError`: wrong content-type, dropped connection, no terminal frame) falls
   * through to the mutation that has carried every turn since M4. A frame the graph itself
   * produced (an `error` event) is not that — it is a real turn failure and is reported as one,
   * identically on both transports. */
  const sendTurn = useCallback((message: string, replaceId?: string) => {
    if (!message) return;

    const pendingId = nextId();
    const sentAt = new Date().toISOString();
    setTurns((prev) =>
      replaceId
        ? prev.map((t) => (t.id === replaceId ? { kind: "pending", id: pendingId, stages: [] } : t))
        : [
            ...prev,
            { kind: "user", id: nextId(), content: message, createdAt: sentAt },
            { kind: "pending", id: pendingId, stages: [] },
          ],
    );
    setShowAskHint(false);
    // A new turn's evidence replaces the old: leaving the previous selection active would point
    // a highlight at a row that is no longer on screen.
    setActiveCitation(null);
    setSelectedTurnId(null);
    // A fresh turn is exactly what "following conversation" means — day view was a detour from
    // that, and asking something new is the clearest signal the detour is over.
    setDayView(null);

    const onSuccess = (response: ChatResponse) => {
      setThreadId(response.thread_id);
      // Step 6 of §9.1: hand off to asking, but only after the first *created* memory.
      if (response.receipts.some((r) => r.created.length > 0) && turns.length === 0) {
        setShowAskHint(true);
      }
      setTurns((prev) =>
        prev.map((turn) =>
          turn.id === pendingId
            ? {
                kind: "assistant",
                id: pendingId,
                content: response.answer,
                receipts: response.receipts,
                citations: response.citations,
                citationReport: response.citation_report,
                trace: response.trace,
                turnId: response.turn_id,
                errors: response.errors,
                createdAt: new Date().toISOString(),
              }
            : turn,
        ),
      );
    };

    // The turn failed, and the user is told so in place — with the message kept and one action
    // to resend it. Dropping it silently and refilling the composer looked like the message had
    // bounced for no reason (§6.11).
    const onFailure = (error: unknown) => {
      const detail =
        error instanceof ApiError && error.status >= 500
          ? "The server couldn't complete it."
          : error instanceof ApiError
            ? error.message
            : "Couldn't reach the server.";
      setTurns((prev) =>
        prev.map((t) =>
          t.id === pendingId ? { kind: "failed" as const, id: pendingId, message, detail } : t,
        ),
      );
    };

    streamAbortRef.current?.abort();
    const controller = new AbortController();
    streamAbortRef.current = controller;

    void (async () => {
      // Declared outside the try block deliberately: a `done` frame can arrive and then the
      // connection can still throw while the reader waits for EOF (rare, but real — a proxy that
      // delivers the payload and then hangs the close). If that happens, `payload` is the one
      // signal that the turn actually completed, and the catch block below must be able to see
      // it — treating a post-completion transport hiccup as either "retry" or "failed" would
      // silently duplicate the turn or lose an answer that had already arrived.
      let payload: ChatResponse | null = null;
      try {
        for await (const event of streamChat(message, threadId, controller.signal)) {
          if (event.type === "stage") {
            setTurns((prev) =>
              prev.map((t) =>
                t.id === pendingId && t.kind === "pending"
                  ? { ...t, stages: [...t.stages, event.label] }
                  : t,
              ),
            );
          } else {
            payload = event.payload;
          }
        }
        if (!payload) throw new StreamUnavailableError("stream ended without a payload");
        onSuccess(payload);
        invalidateAfterTurn();
      } catch (err) {
        if (err instanceof DOMException && err.name === "AbortError") return; // superseded
        if (payload) {
          // The done frame already arrived — whatever broke after it is irrelevant to the turn.
          onSuccess(payload);
          invalidateAfterTurn();
          return;
        }
        if (err instanceof StreamUnavailableError) {
          // Nothing was observed to happen server-side yet — safe to retry silently.
          send.mutate(threadId ? { message, threadId } : { message }, { onSuccess, onError: onFailure });
          return;
        }
        // StreamInterruptedError (the graph was seen running — an ingest may already have
        // committed — before the connection broke) falls through to here deliberately, along
        // with every other error: surfaced as a real failure, same UX as any other failed turn,
        // never silently resent.
        onFailure(err);
      }
    })();
    // `turns.length` (not `turns`): only the "was this the first turn" check in `onSuccess`
    // reads it, so the identity only needs to change when the *count* does, not on every
    // in-place turn update (streaming stages, retries) that leaves the length untouched.
  }, [threadId, turns.length, send.mutate, invalidateAfterTurn]);

  /**
   * The day-view handoff from Review (`routes/Review.tsx`).
   *
   * Review has **no day-view renderer of its own** (§6.20's "no machinery" rule) — clicking its
   * timeline, an insight, or a memory row navigates here carrying `state.day`, and this effect
   * completes the action. That keeps exactly one day-view renderer in the product.
   *
   * **The draft hand-off this effect used to also handle is gone** (2026-08-13 IA revision):
   * Today used to carry `state.draft` from its own composer, but Review has no composer — Chat
   * is home now, so the shortcut a landing-page composer existed for is no longer needed.
   *
   * **The state is cleared before acting.** A `replace` navigation strips it, so a browser back
   * or a refresh cannot re-trigger the scrub. That clear, not the dependency array, is the
   * guard — `handleScrub`'s identity is stable, but clearing prevents a stale `state.day` from
   * re-firing on an unrelated re-render. Keyed on `location.key` instead — one run per
   * navigation, which is exactly the event being handled.
   */
  useEffect(() => {
    const handoff = location.state as { day?: string } | null;
    if (!handoff?.day) return;
    void navigate(location.pathname, { replace: true, state: null });
    handleScrub(handoff.day);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [location.key]);

  function handleSubmit() {
    const message = draft.trim();
    if (!message) return;
    setDraft("");
    sendTurn(message);
  }

  /**
   * The one place the active thread changes — "New chat" and picking a thread from the sidebar
   * both funnel through here, the only difference being where `newThreadId` comes from (freshly
   * minted vs. an id the sidebar already had). Nothing here calls a delete endpoint or even a
   * mutation: it swaps which thread is active (`session/threadStore.ts`), which changes
   * `useTurns`'s query key — the thread being left is exactly as it was, just no longer the one
   * on screen.
   *
   * `seeded.current = false` is what makes switching TO an existing thread actually load its
   * history: the seeding effect above only ever runs once per truthy value of that flag (by
   * design — re-seeding an already-open thread would drop its receipts), so re-arming it here is
   * what lets the effect treat the newly active thread as unseeded again.
   */
  function switchToThread(newThreadId: string) {
    streamAbortRef.current?.abort();
    setActiveThread(newThreadId);
    setThreadId(newThreadId);
    seeded.current = false;
    setTurns([]);
    setSelectedTurnId(null);
    setActiveCitation(null);
    setDayView(null);
    setShowAskHint(false);
    setDrawerOpen(false);
  }

  function handleNewChat() {
    switchToThread(startNewThread());
  }

  /** `ThreadSidebarRail` already no-ops a re-click on the already-active thread (and still
   * closes its own mobile drawer when it happens) — this is only ever called with a genuinely
   * different id. */
  function handleSelectThread(id: string) {
    switchToThread(id);
  }

  return (
    <div className="relative flex h-dvh flex-col overflow-hidden">
      <TopBar isBusy={isSending} />

      <div className="relative flex min-h-0 flex-1 overflow-hidden">
        {/* Sidebar rail — extracted to `ThreadSidebarRail` (§16 Decisions Log, amended
            2026-08-12) so `Today` can carry the exact same sidebar + account row, ChatGPT's own
            layout. Sits below the header/timeline, in the same row as the conversation and the
            evidence pane, mirroring that pane's split rather than spanning the full app height
            above it. */}
        <ThreadSidebarRail
          activeThreadId={threadId}
          onSelectThread={handleSelectThread}
          onNewChat={handleNewChat}
        />

        <main className="flex min-w-0 flex-1 flex-col">
          {stats.isPending ? (
            <div className="mx-auto flex w-full max-w-conversation flex-col gap-4 px-4 py-8 md:px-8">
              <Skeleton className="h-4 w-40" />
              <Skeleton className="h-4 w-full max-w-[52ch]" />
              <Skeleton className="h-4 w-2/3" />
            </div>
          ) : stats.isError && !sessionExpired ? (
            <div className="mx-auto w-full max-w-conversation px-4 py-8 md:px-8">
              <ErrorState
                size="page"
                title="Couldn't reach your memory"
                detail="The database is unreachable right now."
                preserved="Nothing you logged has been lost."
                onRetry={() => void stats.refetch()}
              />
            </div>
          ) : isEmptyAccount ? (
            <div className="mx-auto flex w-full max-w-conversation flex-1 px-4 md:px-8">
              <FirstRun onPick={setDraft} isDimmed={draft.length > 0} />
            </div>
          ) : (
            <Conversation
              turns={turns}
              rows={rows}
              missing={missing}
              activeId={activeCitation}
              onActivateCitation={activateCitation}
              onRetry={sendTurn}
              scrubDay={scrubDay}
              onScrubHandled={clearScrubDay}
            />
          )}

          <div className="shrink-0">
            {showAskHint && (
              <div className="mx-auto w-full max-w-conversation px-4 pb-2 md:px-8">
                <FirstAskHint isReduced={Boolean(reduce)} />
              </div>
            )}
            {sessionExpired && <SessionNotice />}
            <div className="mx-auto w-full max-w-conversation px-4 pb-4 pt-2 md:px-8">
              <Composer
                value={draft}
                onChange={setDraft}
                onSubmit={handleSubmit}
                isLocked={sessionExpired}
                isSending={isSending}
              />
            </div>
          </div>
        </main>

        {/* Fixed 420px when open — never flexes, evidence rows have a natural width and
            stretching them on a wide monitor makes them harder to read, not easier (§5.7) — or 0
            when the user collapses it. `overflow-hidden` clips the pane's own content during
            that width transition instead of letting it wrap/reflow mid-animation. */}
        <div
          className={cn(
            "hidden shrink-0 overflow-hidden transition-[width] duration-medium ease-move lg:block",
            isPaneCollapsed ? "w-0" : "w-pane",
          )}
        >
          <div className="h-full w-pane">
            <EvidencePane {...paneProps} isBusy={isSending} />
          </div>
        </div>

        {/* The handle sits ON the border between the two columns on desktop — centered on it, not
            beside it: `right` is offset by half the button's own 32px so its CENTER, not its
            edge, lands on the boundary. Plain `calc()` on both axes rather than `top-1/2` +
            `-translate-y-1/2`/`translate-x-1/2`, which measurably failed to hit-test correctly
            here (confirmed live: `elementsFromPoint` reported this button on top, yet neither a
            real mouse click nor Playwright's own actionability check could reach it — a
            transform-vs-hit-testing mismatch, not a stacking/z-index problem, since a direct
            `.click()` on the same element worked and correctly flipped the state).

            Below `lg` there is no border to sit on — the pane is a drawer (§5.8), and this same
            handle becomes its manual open/close, always at the screen's right edge, rather than
            existing only for the citation gesture. `hidden`/`lg:block` (desktop-only visibility)
            lived on THIS wrapper during an earlier, desktop-only version of this feature, not on
            the `Button` itself — confirmed live (390px/834px viewports) that they don't work when
            applied directly to `Button`: its base classes always include `inline-flex`, which
            fights a consumer's `hidden`/`lg:flex` for the `display` property since `cn` does not
            dedupe conflicting utilities (Tailwind resolves the conflict by generated-CSS order,
            not JSX order — the same class of bug `Button.tsx`'s own `size="icon"` comment already
            warns about). Kept as a wrapper now that the button renders on every breakpoint, since
            the underlying hazard (never put a bare `display`-changing className on `Button`)
            doesn't go away just because there's no longer a breakpoint gate on this one. */}
        <div
          className={cn(
            "absolute top-[calc(50%-16px)] z-10",
            "transition-[right] duration-medium ease-move",
            isDesktop && !isPaneCollapsed
              ? "right-[calc(var(--container-pane)-16px)]"
              : "-right-4",
          )}
        >
          <Button
            type="button"
            variant="secondary"
            size="icon"
            onClick={() => (isDesktop ? setPaneCollapsed((v) => !v) : setDrawerOpen((v) => !v))}
            aria-label={
              (isDesktop ? isPaneCollapsed : !isDrawerOpen)
                ? "Show memory engine"
                : "Hide memory engine"
            }
            aria-expanded={isDesktop ? !isPaneCollapsed : isDrawerOpen}
          >
            {(isDesktop ? isPaneCollapsed : !isDrawerOpen) ? (
              <PanelRightOpen className="size-4" strokeWidth={1.5} aria-hidden="true" />
            ) : (
              <PanelRightClose className="size-4" strokeWidth={1.5} aria-hidden="true" />
            )}
          </Button>
        </div>
      </div>

      {/* Below lg the evidence pane is a drawer, opened by the citation gesture itself (§5.8) —
          the sidebar's own mobile drawer lives inside `ThreadSidebarRail` now, opened by its own
          handle (there is no equivalent gesture to couple it to — "New chat" and the thread list
          are entry points, not a claim → proof link). */}
      {!isDesktop && (
        <EvidenceDrawer isOpen={isDrawerOpen} onOpenChange={setDrawerOpen} {...paneProps} />
      )}
    </div>
  );
}
