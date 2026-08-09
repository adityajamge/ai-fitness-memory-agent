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

import { memo, useEffect, useRef, useState } from "react";
import { m, useReducedMotion } from "motion/react";
import type { MemoryRow } from "@/api/schemas";
import { Answer } from "@/components/glassbox/Answer";
import { Receipt } from "@/components/glassbox/Receipt";
import { Logo } from "@/components/Logo";
import { ErrorState } from "@/components/state/ErrorState";
import { cn } from "@/lib/utils";
import type { ChatTurn } from "@/types/turn";

/**
 * Staged progress while a turn runs — DESIGN.md §6.10, §9.1 step 3 (amended 2026-08-09: a
 * connected trail, not a single line that overwrites itself — see the Decisions Log).
 *
 * Every `stage` arrives from a real `event: stage` SSE frame (M6) and nothing else — there is no
 * elapsed-time fallback, because a timer standing in for progress is exactly what §6.10 rules
 * out. Until the first frame lands (or on the plain-transport fallback, which carries none), only
 * the pulse shows: an honest "something is happening" with no claim about what stage.
 */
function PendingTurn({ stages, isReduced }: { stages: string[]; isReduced: boolean }) {
  return (
    <m.div
      initial={isReduced ? false : { opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: isReduced ? 0 : 0.18 }}
      className="flex gap-3"
      aria-live="polite"
    >
      <Logo size={28} glowStrength={0} className="mt-0.5" />

      {stages.length === 0 ? (
        <span className="flex h-6 items-center">
          <span
            className={cn("size-1.5 rounded-full bg-signal", !isReduced && "animate-pulse")}
          />
        </span>
      ) : (
        // Each completed stage stays on screen as a dimmed, static dot; the current one keeps
        // the pulse. The connecting line (`--border`, never `--signal` — rule 7) is what makes it
        // read as one continuous path the engine walked, rather than a list of unrelated events.
        <ol className="flex flex-col gap-1.5 border-l border-border py-0.5 pl-3">
          {stages.map((label, i) => {
            const isCurrent = i === stages.length - 1;
            return (
              <m.li
                key={i}
                initial={isReduced ? false : { opacity: 0, x: -4 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ duration: isReduced ? 0 : 0.18 }}
                className="relative flex items-center"
              >
                <span
                  aria-hidden="true"
                  className={cn(
                    "absolute -left-3.75 size-1.5 rounded-full",
                    isCurrent ? cn("bg-signal", !isReduced && "animate-pulse") : "bg-faint",
                  )}
                />
                <span
                  className={cn(
                    "font-mono text-meta",
                    isCurrent ? "text-muted-foreground" : "text-faint",
                  )}
                >
                  {label}…
                </span>
              </m.li>
            );
          })}
        </ol>
      )}
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
  /** Fired with the matched turn's id so `AppScreen` can select it — clicking a timeline bar
   * loads that day's memory into the engine pane, not just scrolls to it. */
  onScrubMatched?: (turnId: string) => void;
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
    return <PendingTurn stages={turn.stages} isReduced={Boolean(reduce)} />;
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
      className="flex gap-3"
    >
      <Logo size={28} glowStrength={0} className="mt-0.5" />

      <div className="flex min-w-0 flex-1 flex-col gap-2.5">
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

        {/* §6.6: the third citation state. Quiet, because an uncited answer is a narrator
            weakness rather than a system failure — but never hidden, or the validator has no
            teeth. */}
        {turn.citationReport?.status === "uncited" && (
          <p className="text-meta text-faint">
            answered without citing evidence ·{" "}
            <span className="font-mono">{turn.citationReport.citable_count}</span> memories were
            available
          </p>
        )}
      </div>
    </m.div>
  );
}

/**
 * Memoized: this is the most expensive tree in the app (every turn re-parses its citation
 * markers — see `Answer`), and its props never change on a composer keystroke. Without `memo`,
 * `AppScreen` re-rendering on every character typed drags the whole conversation along with it,
 * which is what "typing feels heavy" actually was.
 */
export const Conversation = memo(function Conversation({
  turns,
  scrubDay,
  onScrubHandled,
  onScrubMatched,
  ...rest
}: ConversationProps) {
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
      // The assistant turn, specifically — it's the one carrying the trace/receipts, so it's
      // what `onScrubMatched` needs to load that day's memory into the engine pane. A day whose
      // only match is the user's message (its answer hasn't loaded, or was clipped from the
      // in-memory `turns` list) has nothing to select, so it's left alone rather than pointing
      // the pane at a turn with no evidence.
      const evidenceTurn = match.kind === "assistant" ? match : turns.find(
        (t) => t.kind === "assistant" && t.createdAt?.slice(0, 10) === scrubDay,
      );
      if (evidenceTurn) onScrubMatched?.(evidenceTurn.id);
      onScrubHandled?.();
      return () => clearTimeout(timer);
    }
    onScrubHandled?.();
    return undefined;
  }, [scrubDay, turns, reduce, onScrubHandled, onScrubMatched]);

  // A returning user (this only renders once `AppScreen` has already ruled out an empty
  // account — see its `isEmptyAccount` branch) starting a fresh thread otherwise saw nothing
  // here at all: a blank pane between the timeline and the composer, with no indication the
  // account's history is still there. Quiet, not a repeat of `FirstRun` — no example prompts,
  // because this is not someone's first time.
  if (turns.length === 0) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-4 px-4 py-6 text-center">
        <m.div
          initial={reduce ? false : { opacity: 0, y: 6 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: reduce ? 0 : 0.32, ease: [0.2, 0, 0, 1] }}
          className="flex flex-col items-center gap-4"
        >
          <Logo size={112} />
          <div className="flex flex-col gap-1.5">
            <p className="text-lead text-foreground">Ask something.</p>
            <p className="max-w-[44ch] text-meta text-muted-foreground">
              Everything you've logged is already in memory — ask about it, or log more.
            </p>
          </div>
        </m.div>
      </div>
    );
  }

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
});
