/**
 * Citation chip — DESIGN.md §6.6. **The signature component of the product.**
 *
 * This is what converts "an LLM said a thing" into "a database proved a thing": every factual
 * claim carries a chip, and clicking it walks the user to the row that backs it.
 *
 * Four rules the implementation enforces rather than assumes:
 *
 * 1. **It is a real `<button>`.** Not a span with onClick. Keyboard users get it for free, and
 *    the a11y tree names it.
 * 2. **Its text comes from the hydrated database row**, never from parsing narration (ADR-12).
 *    The marker in the answer supplies only an ID.
 * 3. **Invalid and unresolvable states are visible, never silently dropped.** A narrator that
 *    cites something that does not resolve is exactly what the mechanical validator exists to
 *    catch, and hiding it here would waste the catch.
 * 4. **Honest scope (ADR-13.13):** a valid citation "resolves", it is not "verified". The tooltip
 *    says so; no copy in this component ever claims the claim itself was checked.
 */

import type { MemoryRow } from "@/api/schemas";
import { cn } from "@/lib/utils";
import { isEstimated, readNutrition } from "./NutritionDerivation";

/**
 * Four states, and the distinction between the last two is a correctness matter, not a nicety.
 *
 * `unresolvable` is a claim: the batch endpoint reported this id in `missing`, so the memory was
 * superseded or removed. `unloaded` is an admission: this turn's trace has not been fetched, so we
 * do not know. Collapsing them would make the UI assert a deletion that may not have happened —
 * exactly the kind of confident-but-wrong statement the glass box exists to prevent.
 */
export type ChipState = "resolved" | "invalid" | "unresolvable" | "unloaded";

function shortDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

/**
 * The value a chip shows, derived from the row's typed payload.
 *
 * Reads only well-known numeric hot fields and falls back to the type name. Deliberately shallow:
 * this renders a label, not a summary, and inventing prose from a payload would be the same
 * mistake as reading it out of the narration.
 *
 * The `~` on an estimated macro is not decoration. A meal's protein is estimated by a model
 * from the foods the user named, and a chip that renders it identically to a transcribed
 * weight or a lab value would quietly promote an estimate to a measurement — in the one
 * component whose entire job is to say where a number came from.
 */
function chipLabel(row: MemoryRow): string {
  const payload = row.payload as Record<string, unknown>;
  const nutrition = readNutrition(payload);
  const protein = nutrition?.protein_g;
  if (typeof protein === "number") {
    return `${nutrition && isEstimated(nutrition) ? "~" : ""}${protein}g protein`;
  }

  for (const key of ["body_fat_pct", "weight_kg", "duration_min", "hours"]) {
    const value = payload[key];
    if (typeof value === "number") return `${value}${key === "body_fat_pct" ? "%" : ""}`;
  }
  return row.type;
}

export interface CitationChipProps {
  id: string;
  /** Absent when the ID could not be hydrated — the chip renders its unresolvable state. */
  row?: MemoryRow | undefined;
  state: ChipState;
  isActive: boolean;
  onActivate: (id: string) => void;
}

export function CitationChip({ id, row, state, isActive, onActivate }: CitationChipProps) {
  const label =
    state === "invalid"
      ? "unresolved citation"
      : row
        ? `${shortDate(row.event_time)} · ${chipLabel(row)}`
        : state === "unloaded"
          ? "evidence not loaded"
          : "memory unavailable";

  const title =
    state === "invalid"
      ? "this citation could not be resolved to a memory"
      : state === "unresolvable"
        ? "this memory was superseded or removed"
        : state === "unloaded"
          ? "this turn's evidence has not been loaded"
          : `citation resolves to memory ${id.slice(0, 8)}`;

  return (
    <button
      type="button"
      // Disabled would remove it from the tab order and hide a state the user should be able to
      // reach and read. It stays focusable and simply does nothing on click.
      onClick={state === "resolved" ? () => onActivate(id) : undefined}
      aria-pressed={state === "resolved" ? isActive : undefined}
      title={title}
      className={cn(
        "mx-0.5 inline-flex items-baseline gap-1 rounded-xs border px-1.5 py-0.5 align-baseline",
        "font-mono text-meta whitespace-nowrap transition-colors duration-120 ease-out",
        state === "resolved" && [
          "cursor-pointer border-signal/40 bg-signal-dim text-foreground",
          "hover:border-signal hover:bg-signal-active",
          isActive && "border-signal bg-signal-active",
        ],
        state === "invalid" && "cursor-not-allowed border-invalid bg-invalid-dim text-invalid",
        (state === "unresolvable" || state === "unloaded") &&
          "cursor-not-allowed border-dashed border-border text-faint",
      )}
    >
      {state === "invalid" && (
        <span aria-hidden="true" className="text-[10px] leading-none">
          ⊘
        </span>
      )}
      {label}
    </button>
  );
}
