/**
 * EmptyState — DESIGN.md §6.9.
 *
 * Empty is a **feature here, not a fallback**. Every account starts empty (ADR-13.4), including
 * every judge's, so this component renders on the most-seen screen in the product rather than on
 * an edge case.
 *
 * The shape is always the same and the API enforces it: a `title` that states the situation and a
 * `body` that says what to do about it. "No data available" is a status; "Your memory starts here"
 * plus an invitation is a design. Making `body` required is what stops the first one happening.
 *
 * No illustrations. A drawing where content belongs is decoration standing in for a decision.
 */

import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

export interface EmptyStateProps {
  title: string;
  /** Required by design: an empty state without a next step is just an error with nicer words. */
  body: string;
  /** Optional affordance — example prompts, a primary action. */
  children?: ReactNode;
  /** `page` centers in the available space; `pane` is compact for the engine pane. */
  size?: "page" | "pane";
  className?: string;
}

export function EmptyState({
  title,
  body,
  children,
  size = "page",
  className,
}: EmptyStateProps) {
  const isPage = size === "page";
  return (
    <div
      className={cn(
        "flex flex-col",
        isPage ? "gap-3" : "gap-2 px-4 py-6",
        className,
      )}
    >
      <h2 className={cn(isPage ? "text-h2" : "text-dense font-medium", "text-foreground")}>
        {title}
      </h2>
      <p
        className={cn(
          isPage ? "text-lead" : "text-meta",
          "max-w-[52ch] text-muted-foreground",
        )}
      >
        {body}
      </p>
      {children && <div className={cn(isPage ? "mt-3" : "mt-1")}>{children}</div>}
    </div>
  );
}
