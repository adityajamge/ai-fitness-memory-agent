/**
 * ToggleGroup — a row of exclusive-choice buttons (sex, goal, activity level, units).
 *
 * Shared by `Onboarding` and `ProfileSettings` (DESIGN.md §6.19) — the only two screens this
 * shape appears on. Built from the existing `Button` component rather than a bespoke control:
 * no new variant, no pill radius, same `active:translate-y-px` press feedback as everywhere else.
 *
 * **Memoized** (fixed 2026-08-13): both call sites are one large form component with every
 * field's state co-located, so a keystroke in an unrelated `Field` re-renders the whole tree —
 * a ToggleGroup instance renders up to five `Button` children, the single most expensive thing
 * in that tree to redo on every keystroke for no reason. `React.memo` only pays off if the
 * props are actually stable, which is the caller's job: `options` and `labelOf` must be
 * module-level constants/functions, never an inline array literal or arrow function, or the
 * memo comparison fails on every render anyway and this component does nothing.
 */

import { memo } from "react";
import { Button } from "./Button";

export interface ToggleGroupProps<T extends string> {
  options: readonly T[];
  value: T | null;
  onChange: (v: T) => void;
  labelOf: (v: T) => string;
}

function ToggleGroupInner<T extends string>({
  options,
  value,
  onChange,
  labelOf,
}: ToggleGroupProps<T>) {
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

// `memo` erases the generic type parameter, so the export is cast back to the generic function
// signature — call sites still get full `<T extends string>` inference, only the runtime
// wrapper changes.
export const ToggleGroup = memo(ToggleGroupInner) as typeof ToggleGroupInner;
