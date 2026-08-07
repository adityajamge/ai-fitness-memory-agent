/**
 * Minimal class-name joiner.
 *
 * Deliberately not `clsx` + `tailwind-merge`: conflict-merging exists to reconcile classes that
 * fight each other, and in this codebase two conflicting utilities on one element means the
 * component is wrong, not that a merge is needed. Keeping this dumb makes that visible.
 */
export function cn(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}
