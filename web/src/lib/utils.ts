/**
 * Minimal class-name joiner.
 *
 * Deliberately not `clsx` + `tailwind-merge`: conflict-merging exists to reconcile classes that
 * fight each other, and in this codebase two conflicting utilities on one element means the
 * component is wrong, not that a merge is needed. That is not theoretical — M4's send button
 * rendered 0px wide because a `px-0` override lost to a variant's `px-4`, and the right fix was
 * a missing `size="icon"`, not a merge that would have hidden the design gap.
 *
 * Nested arrays are flattened so conditional groups can be written inline. Flattening is not
 * merging: the last conflicting class still does not win, which is the property worth keeping.
 */

type ClassValue = string | false | null | undefined | ClassValue[];

export function cn(...parts: ClassValue[]): string {
  const out: string[] = [];
  for (const part of parts) {
    if (!part) continue;
    if (Array.isArray(part)) {
      const nested = cn(...part);
      if (nested) out.push(nested);
    } else {
      out.push(part);
    }
  }
  return out.join(" ");
}
