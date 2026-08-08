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
import { isUnauthorized, useSendMessage, useStats, useTurns } from "@/api/queries";
import { FirstAskHint, FirstRun } from "@/components/FirstRun";
import { EvidenceDrawer } from "@/components/glassbox/EvidenceDrawer";
import { EvidencePane } from "@/components/glassbox/EvidencePane";
import { useSelectedTrace } from "@/components/glassbox/useSelectedTrace";
import { useTurnEvidence } from "@/components/glassbox/useTurnEvidence";
import { Composer } from "@/components/layout/Composer";
import { Conversation } from "@/components/layout/Conversation";
import { TopBar } from "@/components/layout/TopBar";
import { ErrorState } from "@/components/state/ErrorState";
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
  const sessionExpired = useSessionExpired();
  const reduce = useReducedMotion();

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
          ? { kind: "user" as const, id: t.id, content: t.content ?? "" }
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
            },
    );
    setTurns((prev) => [...seededTurns, ...prev]);
  }, [history.data]);

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
   * so the conversation does not grow a duplicate every time a retry succeeds. */
  function sendTurn(message: string, replaceId?: string) {
    if (!message) return;

    const pendingId = nextId();
    setTurns((prev) =>
      replaceId
        ? prev.map((t) => (t.id === replaceId ? { kind: "pending", id: pendingId } : t))
        : [...prev, { kind: "user", id: nextId(), content: message }, { kind: "pending", id: pendingId }],
    );
    setShowAskHint(false);
    // A new turn's evidence replaces the old: leaving the previous selection active would point
    // a highlight at a row that is no longer on screen.
    setActiveCitation(null);
    setSelectedTurnId(null);

    send.mutate(
      threadId ? { message, threadId } : { message },
      {
        onSuccess: (response) => {
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
                  }
                : turn,
            ),
          );
        },
        onError: (error) => {
          // The turn failed, and the user is told so in place — with the message kept and one
          // action to resend it. Dropping it silently and refilling the composer looked like the
          // message had bounced for no reason (§6.11).
          const detail =
            error instanceof ApiError && error.status >= 500
              ? "The server couldn't complete it."
              : error instanceof ApiError
                ? error.message
                : "Couldn't reach the server.";
          setTurns((prev) =>
            prev.map((t) =>
              t.id === pendingId
                ? { kind: "failed" as const, id: pendingId, message, detail }
                : t,
            ),
          );
        },
      },
    );
  }

  function handleSubmit() {
    const message = draft.trim();
    if (!message) return;
    setDraft("");
    sendTurn(message);
  }

  return (
    <div className="flex h-dvh flex-col overflow-hidden">
      <TopBar isBusy={send.isPending} />

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
                isSending={send.isPending}
              />
            </div>
          </div>
        </main>

        {/* Fixed 420px, never flexes: evidence rows have a natural width and stretching them on a
            wide monitor makes them harder to read, not easier (§5.7). */}
        <div className="hidden w-pane shrink-0 lg:block">
          <EvidencePane {...paneProps} isBusy={send.isPending} />
        </div>
      </div>

      {/* Below lg the same pane is a drawer, opened by the citation gesture itself (§5.8). */}
      {!isDesktop && (
        <EvidenceDrawer isOpen={isDrawerOpen} onOpenChange={setDrawerOpen} {...paneProps} />
      )}
    </div>
  );
}
