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

import { useEffect, useRef } from "react";
import { m, useReducedMotion } from "motion/react";
import type { ChatTurn } from "@/types/turn";
import { Receipt } from "@/components/glassbox/Receipt";
import { ErrorState } from "@/components/state/ErrorState";
import { cn } from "@/lib/utils";

/** Staged progress while a turn runs — DESIGN.md §6.10. Driven by elapsed time only until the
 * graph emits real stage events (M6); the labels never claim more than "still working". */
function PendingTurn({ isReduced }: { isReduced: boolean }) {
  return (
    <m.div
      initial={isReduced ? false : { opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: isReduced ? 0 : 0.18 }}
      className="flex items-center gap-2 text-meta text-faint"
      aria-live="polite"
    >
      <span className="size-1.5 animate-pulse rounded-full bg-signal" />
      <span className="font-mono">assembling context…</span>
    </m.div>
  );
}

function TurnBlock({ turn }: { turn: ChatTurn }) {
  const reduce = useReducedMotion();

  if (turn.kind === "pending") return <PendingTurn isReduced={Boolean(reduce)} />;

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
        // 72ch: the cap that makes this readable on a wide monitor.
        <p className="max-w-[72ch] text-body whitespace-pre-wrap text-foreground">
          {turn.content}
        </p>
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

export function Conversation({ turns }: { turns: ChatTurn[] }) {
  const endRef = useRef<HTMLDivElement>(null);

  // Pin to the newest turn. `auto` rather than `smooth`: a long smooth scroll on every token
  // fights the user if they scrolled up to read, and theme.css disables smooth under reduced
  // motion anyway.
  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [turns.length]);

  return (
    <div className="flex-1 overflow-y-auto px-4 py-6 md:px-8">
      {/* The column centers and caps; extra viewport width becomes margin, not measure. */}
      <div className={cn("mx-auto flex w-full max-w-[860px] flex-col gap-6")}>
        {turns.map((turn) => (
          <TurnBlock key={turn.id} turn={turn} />
        ))}
        <div ref={endRef} />
      </div>
    </div>
  );
}
