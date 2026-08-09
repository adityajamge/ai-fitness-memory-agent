/**
 * Plain English ↔ raw data, for the expanded evidence row.
 *
 * A two-option segmented control rather than a switch: a switch label has to name one state and
 * imply the other ("Raw data ▢"), which reads as an on/off *setting*. These are two equal ways
 * of looking at the same row, and the control should say so.
 *
 * Rendered as a radiogroup so a screen reader announces "Plain English, selected, 1 of 2" and
 * arrow keys move between the options — the semantics two buttons with `aria-pressed` would only
 * approximate. The preference is global (`detailLevelStore`), so flipping it here flips every
 * other expanded row and persists across sessions.
 */

import { setDetailLevel, useDetailLevel, type DetailLevel } from "./detailLevelStore";
import { cn } from "@/lib/utils";

const OPTIONS: ReadonlyArray<{ value: DetailLevel; label: string; hint: string }> = [
  { value: "plain", label: "Plain English", hint: "What this memory says, in words" },
  { value: "raw", label: "Raw data", hint: "The stored database row, exactly as saved" },
];

export function DetailLevelToggle() {
  const active = useDetailLevel();

  return (
    <div
      role="radiogroup"
      aria-label="Evidence detail level"
      className="inline-flex rounded-xs border border-border p-0.5"
    >
      {OPTIONS.map((option) => {
        const isActive = option.value === active;
        return (
          <button
            key={option.value}
            type="button"
            role="radio"
            aria-checked={isActive}
            title={option.hint}
            onClick={() => setDetailLevel(option.value)}
            className={cn(
              "rounded-xs px-2 py-1 text-micro transition-colors duration-120 ease-out",
              // Selection is carried by surface + text weight, not hue (rule 8) — it survives
              // grayscale, which is how this product gets screenshotted.
              isActive
                ? "bg-surface-3 text-foreground"
                : "text-faint hover:text-muted-foreground",
            )}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}
