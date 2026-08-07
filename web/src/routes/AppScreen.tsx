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

import { useEffect, useRef, useState } from "react";
import { Navigate } from "react-router";
import { useReducedMotion } from "motion/react";
import { isUnauthorized, useSendMessage, useStats, useTurns } from "@/api/queries";
import type { EvidenceTrace } from "@/api/schemas";
import { FirstAskHint, FirstRun } from "@/components/FirstRun";
import { EnginePane } from "@/components/glassbox/EnginePane";
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

export function AppScreen() {
  const stats = useStats();
  const history = useTurns();
  const send = useSendMessage();
  const sessionExpired = useSessionExpired();
  const reduce = useReducedMotion();

  const [draft, setDraft] = useState("");
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [trace, setTrace] = useState<EvidenceTrace | null>(null);
  const [threadId, setThreadId] = useState<string>();
  const [showAskHint, setShowAskHint] = useState(false);
  const seeded = useRef(false);

  // Seed from persisted history exactly once. After that the local list is authoritative: it
  // carries receipts, which `GET /api/turns` does not return, and re-seeding would drop them.
  useEffect(() => {
    if (seeded.current || !history.data) return;
    seeded.current = true;
    setTurns(
      history.data.turns.map((t) =>
        t.role === "user"
          ? { kind: "user" as const, id: t.id, content: t.content ?? "" }
          : {
              kind: "assistant" as const,
              id: t.id,
              content: t.content ?? "",
              receipts: [],
              citations: [],
              citationReport: null,
              turnId: t.has_trace ? t.id : null,
              errors: [],
            },
      ),
    );
  }, [history.data]);

  // 401 before any request has ever succeeded means "not signed in", not "expired" — route to
  // login rather than raising a notice over an app the user cannot see anyway.
  if (isUnauthorized(stats.error) && !sessionExpired) {
    return <Navigate to="/login" replace />;
  }

  const isEmptyAccount = stats.data?.memories === 0 && turns.length === 0;

  function handleSubmit() {
    const message = draft.trim();
    if (!message) return;

    const userTurn: ChatTurn = { kind: "user", id: nextId(), content: message };
    const pendingId = nextId();
    setTurns((prev) => [...prev, userTurn, { kind: "pending", id: pendingId }]);
    setDraft("");
    setShowAskHint(false);

    send.mutate(
      threadId ? { message, threadId } : { message },
      {
        onSuccess: (response) => {
          setThreadId(response.thread_id);
          setTrace(response.trace);
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
                    turnId: response.turn_id,
                    errors: response.errors,
                  }
                : turn,
            ),
          );
        },
        onError: () => {
          // The turn failed, but the message is not lost: it returns to the composer so the user
          // can retry without retyping (ADR-13.5's posture, at the UI layer).
          setTurns((prev) => prev.filter((t) => t.id !== pendingId));
          setDraft(message);
        },
      },
    );
  }

  return (
    <div className="flex h-dvh flex-col overflow-hidden">
      <TopBar isBusy={send.isPending} />

      <div className="flex min-h-0 flex-1">
        <main className="flex min-w-0 flex-1 flex-col">
          {stats.isPending ? (
            <div className="mx-auto flex w-full max-w-[860px] flex-col gap-4 px-4 py-8 md:px-8">
              <Skeleton className="h-4 w-40" />
              <Skeleton className="h-4 w-full max-w-[52ch]" />
              <Skeleton className="h-4 w-2/3" />
            </div>
          ) : stats.isError && !sessionExpired ? (
            <div className="mx-auto w-full max-w-[860px] px-4 py-8 md:px-8">
              <ErrorState
                size="page"
                title="Couldn't reach your memory"
                detail="The database is unreachable right now."
                preserved="Nothing you logged has been lost."
                onRetry={() => void stats.refetch()}
              />
            </div>
          ) : isEmptyAccount ? (
            <div className="mx-auto flex w-full max-w-[860px] flex-1 px-4 md:px-8">
              <FirstRun onPick={setDraft} isDimmed={draft.length > 0} />
            </div>
          ) : (
            <Conversation turns={turns} />
          )}

          <div className="shrink-0">
            {showAskHint && (
              <div className="mx-auto w-full max-w-[860px] px-4 pb-2 md:px-8">
                <FirstAskHint isReduced={Boolean(reduce)} />
              </div>
            )}
            {sessionExpired && <SessionNotice />}
            <div className="mx-auto w-full max-w-[860px] px-4 pb-4 pt-2 md:px-8">
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
            wide monitor makes them harder to read, not easier (§5.7). Hidden below lg; the mobile
            drawer lands with the full evidence pane in M6. */}
        <div className="hidden w-[420px] shrink-0 lg:block">
          <EnginePane trace={trace} isBusy={send.isPending} hasTurns={turns.length > 0} />
        </div>
      </div>
    </div>
  );
}
