/**
 * ToggleGroup — a row of exclusive-choice buttons (sex, goal, activity level, units).
 *
 * Shared by `Onboarding` and `ProfileSettings` (DESIGN.md §6.19) — the only two screens this
 * shape appears on. Built from the existing `Button` component rather than a bespoke control:
 * no new variant, no pill radius, same `active:translate-y-px` press feedback as everywhere else.
 */

import { Button } from "./Button";

export function ToggleGroup<T extends string>({
  options,
  value,
  onChange,
  labelOf,
}: {
  options: readonly T[];
  value: T | null;
  onChange: (v: T) => void;
  labelOf: (v: T) => string;
}) {
  return (
    <div className="flex flex-wrap gap-2" role="group">
      {options.map((opt) => (
        <Button
          key={opt}
          type="button"
          variant={value === opt ? "primary" : "secondary"}
          size="md"
          aria-pressed={value === opt}
          onClick={() => onChange(opt)}
        >
          {labelOf(opt)}
        </Button>
      ))}
    </div>
  );
}
