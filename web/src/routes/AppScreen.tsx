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
import { Navigate } from "react-router";
import { useReducedMotion } from "motion/react";
import { ApiError } from "@/api/client";
import { StreamUnavailableError, streamChat } from "@/api/chatStream";
import { isUnauthorized, useInvalidateAfterTurn, useSendMessage, useStats, useTurns } from "@/api/queries";
import type { ChatResponse } from "@/api/schemas";
import { FirstAskHint, FirstRun } from "@/components/FirstRun";
import { EvidenceDrawer } from "@/components/glassbox/EvidenceDrawer";
import { EvidencePane } from "@/components/glassbox/EvidencePane";
import { useSelectedTrace } from "@/components/glassbox/useSelectedTrace";
import { useTurnEvidence } from "@/components/glassbox/useTurnEvidence";
import { Composer } from "@/components/layout/Composer";
import { Conversation } from "@/components/layout/Conversation";
import { TopBar } from "@/components/layout/TopBar";
import { ErrorState } from "@/components/state/ErrorState";
import { Timeline } from "@/components/timeline/Timeline";
import { Skeleton } from "@/components/ui/Skeleton";
import { SessionNotice } from "@/session/SessionNotice";
import { useSessionExpired } from "@/session/sessionStore";
import type { ChatTurn } from "@/types/turn";

let localId = 0;
const nextId = () => `local-${++localId}`;

/** Matches the `lg` breakpoint where the evidence pane becomes a column instead of a drawer. */
const DESKTOP_QUERY = "(min-width: 1024px)";

export function AppScreen() {
  const stats = useStats();
  const history = useTurns();
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
  const [threadId, setThreadId] = useState<string>();
  const [showAskHint, setShowAskHint] = useState(false);
  const [activeCitation, setActiveCitation] = useState<string | null>(null);
  const [isDrawerOpen, setDrawerOpen] = useState(false);
  const [isDesktop, setDesktop] = useState(
    () => typeof window !== "undefined" && window.matchMedia(DESKTOP_QUERY).matches,
  );
  // Set by a timeline click, consumed once by Conversation's scroll-and-highlight effect (§9
  // "click a timeline day"), then cleared here so clicking the same day twice still re-triggers.
  const [scrubDay, setScrubDay] = useState<string | null>(null);
  const timelineRef = useRef<HTMLDivElement>(null);
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

  // The keyboard shortcuts §9 lists that are still worth having without the command palette
  // they were specified alongside (§13: cut-eligible, ranked below everything else in the
  // Phase-6 priority list). `/` (focus composer) and `⌘Enter` (send) already live in Composer;
  // this covers the two that are page-level. `Esc` blurring the active element is the browser's
  // native behavior for most controls, so there is nothing to add for it here.
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null;
      const typing =
        target?.tagName === "INPUT" || target?.tagName === "TEXTAREA" || target?.isContentEditable;
      if (typing) return;
      if (event.key === "e" || event.key === "E") {
        if (!isDesktop) setDrawerOpen((v) => !v);
      } else if (event.key === "t" || event.key === "T") {
        timelineRef.current?.focus();
        timelineRef.current?.scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "start" });
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [isDesktop, reduce]);

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
      if (!isDesktop) setDrawerOpen(true);
    },
    [isDesktop],
  );

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
  function sendTurn(message: string, replaceId?: string) {
    if (!message) return;

    const pendingId = nextId();
    const sentAt = new Date().toISOString();
    setTurns((prev) =>
      replaceId
        ? prev.map((t) => (t.id === replaceId ? { kind: "pending", id: pendingId } : t))
        : [
            ...prev,
            { kind: "user", id: nextId(), content: message, createdAt: sentAt },
            { kind: "pending", id: pendingId },
          ],
    );
    setShowAskHint(false);
    // A new turn's evidence replaces the old: leaving the previous selection active would point
    // a highlight at a row that is no longer on screen.
    setActiveCitation(null);
    setSelectedTurnId(null);

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
      try {
        let payload: ChatResponse | null = null;
        for await (const event of streamChat(message, threadId, controller.signal)) {
          if (event.type === "stage") {
            setTurns((prev) =>
              prev.map((t) =>
                t.id === pendingId && t.kind === "pending" ? { ...t, stage: event.label } : t,
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
        if (err instanceof StreamUnavailableError) {
          send.mutate(threadId ? { message, threadId } : { message }, { onSuccess, onError: onFailure });
          return;
        }
        onFailure(err);
      }
    })();
  }

  function handleSubmit() {
    const message = draft.trim();
    if (!message) return;
    setDraft("");
    sendTurn(message);
  }

  /** §9 "click a timeline day scrubs the conversation to that date" — `scrubDay` is a one-shot
   * signal Conversation consumes, then it is cleared so the same day can be clicked again. */
  function handleScrub(day: string) {
    setScrubDay(day);
  }

  return (
    <div className="flex h-dvh flex-col overflow-hidden">
      <TopBar isBusy={isSending} />

      {/* One of the four designed empty states (§6.9) — rendered unconditionally, so a brand-new
          account shows "your memory starts here" here too rather than nothing. */}
      <div ref={timelineRef} tabIndex={-1} className="outline-none">
        <Timeline onScrub={handleScrub} />
      </div>

      <div className="flex min-h-0 flex-1">
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
              onScrubHandled={() => setScrubDay(null)}
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

        {/* Fixed 420px, never flexes: evidence rows have a natural width and stretching them on a
            wide monitor makes them harder to read, not easier (§5.7). */}
        <div className="hidden w-pane shrink-0 lg:block">
          <EvidencePane {...paneProps} isBusy={isSending} />
        </div>
      </div>

      {/* Below lg the same pane is a drawer, opened by the citation gesture itself (§5.8). */}
      {!isDesktop && (
        <EvidenceDrawer isOpen={isDrawerOpen} onOpenChange={setDrawerOpen} {...paneProps} />
      )}
    </div>
  );
}
