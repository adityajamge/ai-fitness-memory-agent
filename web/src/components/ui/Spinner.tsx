/**
 * Spinner — DESIGN.md §6.10, loading kind 3.
 *
 * **Only for indeterminate actions with no known shape** (sign-in, retry extraction). Content
 * whose shape is known gets a `Skeleton`, and narration gets streamed text. Reaching for a
 * spinner where a skeleton belongs is a contract violation, not a style choice: a spinner tells
 * the user "wait", a skeleton tells them what is arriving.
 *
 * Kept spinning under reduced motion. A rotating 14px glyph is not a vestibular trigger, and
 * freezing it would leave a static circle that reads as broken rather than busy.
 */

import { cn } from "@/lib/utils";

export function Spinner({ className }: { className?: string }) {
  return (
    <svg
      className={cn("size-3.5 animate-spin text-current", className)}
      viewBox="0 0 16 16"
      fill="none"
      aria-hidden="true"
    >
      <circle cx="8" cy="8" r="6.5" stroke="currentColor" strokeWidth="1.5" opacity="0.25" />
      <path
        d="M14.5 8a6.5 6.5 0 0 0-6.5-6.5"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
      />
    </svg>
  );
}
