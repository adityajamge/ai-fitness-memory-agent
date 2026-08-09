/**
 * "How this was retrieved" — DESIGN.md §9, build-order item 6.
 *
 * The actual SQL and vector queries the engine ran, with their bound parameters and row counts.
 * This is the single most scoreable thing in the product: it is the difference between claiming
 * hybrid SQL+vector retrieval and showing it.
 *
 * Everything here comes from `trace.retrieval_steps`, served verbatim (I-29). Nothing is
 * reconstructed, prettified, or re-executed — a displayed query that differs from the one that
 * ran would be worse than showing none.
 */

import { useState } from "react";
import { ChevronRight } from "lucide-react";
import type { RetrievalStep } from "@/api/schemas";
import { ALWAYS_HIDDEN_PARAMS, INTERNAL_PARAMS } from "@/lib/internalFields";
import { cn } from "@/lib/utils";
import { useDetailLevel } from "./detailLevelStore";

function StepBlock({ step, detail }: { step: RetrievalStep; detail: "plain" | "raw" }) {
  // `user_id` goes in both views: every row in this pane belongs to the signed-in account by
  // construction, so printing that account's UUID beside each query tells the reader nothing
  // and hands them an internal identifier for no reason. The `%(user_id)s` placeholder stays
  // in the SQL above, so the property worth seeing — every query is scoped to one account —
  // is still on screen. Engine internals (JSONB paths, the query vector) go in raw only.
  const params = Object.entries(step.params).filter(
    ([key]) =>
      !ALWAYS_HIDDEN_PARAMS.has(key) && (detail === "raw" || !INTERNAL_PARAMS.has(key)),
  );

  return (
    <div className="rounded-md border border-border bg-background p-3">
      <div className="flex items-baseline justify-between gap-3">
        <span className="font-mono text-micro uppercase tracking-[0.08em] text-muted-foreground">
          {step.family}
        </span>
        <span className="font-mono text-meta text-faint">
          {step.row_count} {step.row_count === 1 ? "row" : "rows"}
        </span>
      </div>

      {/* <pre><code> because it is code. Scrolls inside itself so a long query never widens
          the pane and forces the page to scroll horizontally. */}
      <pre className="mt-2 overflow-x-auto">
        <code className="font-mono text-meta leading-relaxed text-muted-foreground">
          {step.sql.trim()}
        </code>
      </pre>

      {params.length > 0 && (
        <dl className="mt-2 flex flex-col gap-0.5 border-t border-border pt-2">
          {params.map(([key, value]) => (
            <div key={key} className="flex gap-2 font-mono text-micro">
              <dt className="shrink-0 text-faint">{key}</dt>
              <dd className="truncate text-muted-foreground">
                {typeof value === "string" ? value : JSON.stringify(value)}
              </dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  );
}

export function RetrievalQueries({ steps }: { steps: RetrievalStep[] }) {
  const [isOpen, setOpen] = useState(false);
  const detail = useDetailLevel();
  if (steps.length === 0) return null;

  return (
    <div className="mt-3 border-t border-border pt-3">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={isOpen}
        className="flex w-full items-center gap-1.5 text-left text-meta text-muted-foreground transition-colors duration-120 hover:text-foreground"
      >
        <ChevronRight
          className={cn(
            "size-3.5 shrink-0 transition-transform duration-180 ease-out",
            isOpen && "rotate-90",
          )}
          strokeWidth={1.5}
          aria-hidden="true"
        />
        how this was retrieved
        <span className="ml-auto font-mono text-faint">
          {steps.length} {steps.length === 1 ? "query" : "queries"}
        </span>
      </button>

      {isOpen && (
        <div className="mt-2 flex flex-col gap-2">
          {steps.map((step, i) => (
            <StepBlock key={i} step={step} detail={detail} />
          ))}
        </div>
      )}
    </div>
  );
}
