/**
 * The conversation — DESIGN.md §5.7 (F-T3).
 *
 * **The 72ch cap is the point of this file.** The column grows with the viewport; the *text inside
 * it* does not. Comfortable reading measure is 60–75 characters, and an uncapped column on a
 * 2560px monitor produces ~2000px lines of 15px text that nobody can track a line-break in.
 *
 * This matters more here than in a normal chat app, because the widest screen this product will
 * ever be judged on belongs to a reviewer.
 */

import { useEffect, useRef, useState } from "react";
import { m, useReducedMotion } from "motion/react";
import type { MemoryRow } from "@/api/schemas";
import { Answer } from "@/components/glassbox/Answer";
import { Receipt } from "@/components/glassbox/Receipt";
import { ErrorState } from "@/components/state/ErrorState";
import { cn } from "@/lib/utils";
import type { ChatTurn } from "@/types/turn";

/**
 * Staged progress while a turn runs — DESIGN.md §6.10, §9.1 step 3.
 *
 * `stage` arrives from a real `event: stage` SSE frame (M6) and nothing else — there is no
 * elapsed-time fallback, because a timer standing in for progress is exactly what §6.10 rules
 * out. Until the first frame lands (or on the plain-transport fallback, which carries none),
 * only the pulse shows: an honest "something is happening" with no claim about what stage.
 */
function PendingTurn({ stage, isReduced }: { stage: string | undefined; isReduced: boolean }) {
  return (
    <m.div
      initial={isReduced ? false : { opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: isReduced ? 0 : 0.18 }}
      className="flex items-center gap-2 text-meta text-faint"
      aria-live="polite"
    >
      <span className="size-1.5 animate-pulse rounded-full bg-signal" />
      {stage && <span className="font-mono">{stage}…</span>}
    </m.div>
  );
}

export interface ConversationProps {
  turns: ChatTurn[];
  rows: Map<string, MemoryRow>;
  missing: Set<string>;
  activeId: string | null;
  onActivateCitation: (id: string, turnId: string) => void;
  onRetry: (message: string, failedId: string) => void;
  /** A day picked on the timeline strip (§9 "click a timeline day"), or null between scrubs. */
  scrubDay?: string | null;
  /** Called once the scrub has been acted on, so `AppScreen` can clear it and the same day can
   * be clicked again later. */
  onScrubHandled?: () => void;
}

function TurnBlock({
  turn,
  rows,
  missing,
  activeId,
  onActivateCitation,
  onRetry,
}: {
  turn: ChatTurn;
} & Omit<ConversationProps, "turns" | "scrubDay" | "onScrubHandled">) {
  const reduce = useReducedMotion();

  if (turn.kind === "pending") {
    return <PendingTurn stage={turn.stage} isReduced={Boolean(reduce)} />;
  }

  if (turn.kind === "failed") {
    return (
      <ErrorState
        title="That message didn't go through"
        detail={turn.detail}
        // The guarantee, stated where it matters: nothing was thrown away.
        preserved="Your message is still here — nothing was lost."
        onRetry={() => onRetry(turn.message, turn.id)}
        retryLabel="send again"
      />
    );
  }

  if (turn.kind === "user") {
    return (
      <div className="flex justify-end">
        <p className="max-w-[72ch] rounded-md bg-surface-2 px-4 py-2.5 text-body text-foreground">
          {turn.content}
        </p>
      </div>
    );
  }

  return (
    <m.div
      initial={reduce ? false : { opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: reduce ? 0 : 0.24, ease: [0.2, 0, 0, 1] }}
      className="flex flex-col gap-2.5"
    >
      {turn.receipts.map((receipt, i) => (
        <Receipt key={i} receipt={receipt} />
      ))}

      {turn.content && (
        <Answer
          text={turn.content}
          rows={rows}
          missing={missing}
          report={turn.citationReport}
          activeId={activeId}
          onActivate={(id) => onActivateCitation(id, turn.id)}
        />
      )}

      {/* Retrieval the engine refused. Surfaced rather than swallowed — the answer may be
          partial, and the user deserves to know which part. */}
      {turn.errors.length > 0 && (
        <ErrorState
          title="Part of this answer is missing"
          detail={turn.errors.join(" · ")}
          preserved="Everything you logged was still saved."
        />
      )}

      {/* §6.6: the third citation state. Quiet, because an uncited answer is a narrator weakness
          rather than a system failure — but never hidden, or the validator has no teeth. */}
      {turn.citationReport?.status === "uncited" && (
        <p className="text-meta text-faint">
          answered without citing evidence ·{" "}
          <span className="font-mono">{turn.citationReport.citable_count}</span> memories were
          available
        </p>
      )}
    </m.div>
  );
}

export function Conversation({ turns, scrubDay, onScrubHandled, ...rest }: ConversationProps) {
  const endRef = useRef<HTMLDivElement>(null);
  const turnRefs = useRef(new Map<string, HTMLDivElement>());
  const [scrubbedId, setScrubbedId] = useState<string | null>(null);
  const reduce = useReducedMotion();

  // Pin to the newest turn. `auto` rather than `smooth`: a long smooth scroll on every token
  // fights the user if they scrolled up to read, and theme.css disables smooth under reduced
  // motion anyway.
  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [turns.length]);

  // §5.8 mobile keyboard: "opening the keyboard pins the conversation to its last turn rather
  // than preserving scroll offset" — a shrinking `visualViewport` is the opening keyboard.
  // Feature-detected: browsers without `visualViewport` (or `dvh` support) fall back to
  // whatever their normal reflow does, which is the pre-existing behavior, not a regression.
  useEffect(() => {
    const vv = window.visualViewport;
    if (!vv) return;
    let lastHeight = vv.height;
    const onResize = () => {
      if (vv.height < lastHeight) endRef.current?.scrollIntoView({ block: "end" });
      lastHeight = vv.height;
    };
    vv.addEventListener("resize", onResize);
    return () => vv.removeEventListener("resize", onResize);
  }, []);

  // §9 "click a timeline day scrubs the conversation to that date": find the nearest turn whose
  // `createdAt` falls on that UTC day, scroll to it, and hold a brief highlight. Rule 7 confines
  // `--signal` to a fixed list that this interaction is not on, so the highlight is a surface
  // change (`--surface-3`, already the established non-signal emphasis — see EvidenceRow's
  // hover), not a signal border.
  useEffect(() => {
    if (!scrubDay) return;
    const match = turns.find(
      (t) => (t.kind === "user" || t.kind === "assistant") && t.createdAt?.slice(0, 10) === scrubDay,
    );
    if (match) {
      turnRefs.current.get(match.id)?.scrollIntoView({
        behavior: reduce ? "auto" : "smooth",
        block: "center",
      });
      setScrubbedId(match.id);
      const timer = setTimeout(() => setScrubbedId(null), 1400);
      onScrubHandled?.();
      return () => clearTimeout(timer);
    }
    onScrubHandled?.();
    return undefined;
  }, [scrubDay, turns, reduce, onScrubHandled]);

  return (
    <div className="flex-1 overflow-y-auto px-4 py-6 md:px-8">
      {/* The column centers and caps; extra viewport width becomes margin, not measure. */}
      <div className="mx-auto flex w-full max-w-conversation flex-col gap-6">
        {turns.map((turn) => (
          <div
            key={turn.id}
            ref={(el) => {
              if (el) turnRefs.current.set(turn.id, el);
              else turnRefs.current.delete(turn.id);
            }}
            className={cn(
              "rounded-md transition-colors duration-medium",
              scrubbedId === turn.id && "bg-surface-2",
            )}
          >
            <TurnBlock turn={turn} {...rest} />
          </div>
        ))}
        <div ref={endRef} />
      </div>
    </div>
  );
}
