/**
 * ErrorState — DESIGN.md §6.11.
 *
 * Two rules the API shape enforces:
 *
 * 1. **Name what failed.** "Something went wrong" tells the user nothing and tells the developer
 *    nothing. `title` is required and should say which thing broke.
 * 2. **Say what survived.** This product's whole posture is that it does not lose what you told
 *    it (ADR-13.5), so an error that stays silent about the input's fate contradicts the product.
 *    `preserved` is how a call site says "your message is still here".
 *
 * A failing pane never breaks the conversation: this renders in place, scoped to the surface that
 * failed, and is never a modal for a recoverable error.
 */

import { AlertTriangle } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { cn } from "@/lib/utils";

export interface ErrorStateProps {
  /** What failed, specifically. Not "an error occurred". */
  title: string;
  /** Optional detail — the server's `detail`, or what the user can do about it. */
  detail?: string | undefined;
  /** What was NOT lost. Rendered in --muted-foreground so it reads as reassurance, not alarm. */
  preserved?: string | undefined;
  onRetry?: (() => void) | undefined;
  retryLabel?: string;
  size?: "page" | "pane";
  className?: string;
}

export function ErrorState({
  title,
  detail,
  preserved,
  onRetry,
  retryLabel = "retry",
  size = "pane",
  className,
}: ErrorStateProps) {
  const isPage = size === "page";
  return (
    <div
      // `alert` rather than `status`: an error that arrives while the user is reading elsewhere
      // should interrupt. Loading and empty states use polite regions instead.
      role="alert"
      className={cn(
        "flex flex-col items-start gap-2",
        isPage ? "gap-3" : "border-l-2 border-invalid bg-invalid-dim/40 px-4 py-3",
        className,
      )}
    >
      <div className="flex items-center gap-2">
        <AlertTriangle
          className={cn("shrink-0 text-invalid", isPage ? "size-5" : "size-4")}
          strokeWidth={1.5}
          aria-hidden="true"
        />
        <p className={cn(isPage ? "text-h2" : "text-dense font-medium", "text-foreground")}>
          {title}
        </p>
      </div>

      {detail && (
        <p className={cn(isPage ? "text-body" : "text-meta", "text-muted-foreground")}>
          {detail}
        </p>
      )}

      {preserved && (
        <p className={cn(isPage ? "text-body" : "text-meta", "text-muted-foreground")}>
          {preserved}
        </p>
      )}

      {onRetry && (
        <Button variant="ghost" size="sm" onClick={onRetry} className="mt-1 -ml-3">
          {retryLabel}
        </Button>
      )}
    </div>
  );
}
