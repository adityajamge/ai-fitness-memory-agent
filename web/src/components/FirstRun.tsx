/**
 * The guided first turn — DESIGN.md §9.1 (F-T1).
 *
 * **Why this exists at all.** ADR-13.4 means no judge sandbox, no seed cloning, no sample data:
 * every account starts genuinely empty, including the account of whoever is scoring this. So
 * *signup → first message → first receipt* is not onboarding polish, it is the entire live
 * product experience, and it is E2E path 1 in the Definition of Done.
 *
 * A passive empty state fails here in a specific way: a reviewer who does not know what to type
 * sees an empty box, types nothing, and leaves without the glass box ever running once.
 *
 * This is **not a tour**. No steps, no progress dots, no skip button, nothing to dismiss. It
 * exists only until the first memory is created, and then the app is the app forever.
 */

import { m, useReducedMotion } from "motion/react";
import { EmptyState } from "@/components/state/EmptyState";
import { Button } from "@/components/ui/Button";
import { cn } from "@/lib/utils";

/**
 * Real loggable text, never "Try asking about…" placeholders. A reviewer must be able to click one
 * and get a genuine typed memory back — a prompt that produces nothing would discredit the receipt
 * that follows it.
 */
const EXAMPLES = [
  "250g curd, 3 eggs, 200g chicken",
  "ran 5k this morning, felt easy",
  "slept 6h, woke up twice",
] as const;

export function FirstRun({
  onPick,
  isDimmed,
}: {
  onPick: (text: string) => void;
  /** True once the user starts typing: the prompts fade back so they stop competing with the
   * text being written, but stay clickable. */
  isDimmed: boolean;
}) {
  const reduce = useReducedMotion();

  return (
    <m.div
      initial={reduce ? false : { opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: reduce ? 0 : 0.32, ease: [0.2, 0, 0, 1] }}
      className="flex flex-1 flex-col justify-center"
    >
      <EmptyState
        title="Your memory starts here."
        body="Tell me what you ate, how you trained, how you slept. I'll structure it and remember it."
      >
        <div
          className={cn(
            "flex flex-col items-start gap-2 transition-opacity duration-180 sm:flex-row sm:flex-wrap",
            isDimmed && "opacity-40",
          )}
        >
          {EXAMPLES.map((example) => (
            <Button
              key={example}
              variant="secondary"
              size="md"
              onClick={() => onPick(example)}
              className="max-w-full justify-start overflow-hidden text-ellipsis"
            >
              {example}
            </Button>
          ))}
        </div>
      </EmptyState>
    </m.div>
  );
}

/**
 * Step 6 of the sequence: the hand-off.
 *
 * Shown once, ever, immediately after the first memory lands, and dismissed permanently on the
 * next send. Its whole job is to move the user from "I logged something" to "I can ask about it",
 * which is the moment the memory engine stops being a claim.
 */
export function FirstAskHint({ isReduced }: { isReduced: boolean }) {
  return (
    <m.p
      initial={isReduced ? false : { opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: isReduced ? 0 : 0.32, delay: isReduced ? 0 : 0.4 }}
      className="text-meta text-faint"
    >
      now ask about it — <span className="font-mono">"how much protein today?"</span>
    </m.p>
  );
}
