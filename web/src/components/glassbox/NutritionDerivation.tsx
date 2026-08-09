/**
 * How a meal's macros were arrived at — DESIGN.md §6.4, §9.
 *
 * The glass box for the one number in this product that is **not** a transcription. A protein
 * total is estimated by a model from the foods the user named, so showing the total alone would
 * be the exact failure this pane exists to prevent: a confident figure with no way to ask where
 * it came from.
 *
 * Four rules, each a consequence of that:
 *
 * 1. **Stated and estimated quantities never look alike.** `stated` is what the user said;
 *    `ai_estimated` is a portion the model chose. They are different *kinds* of fact, so they
 *    get different tags — and, per WCAG 1.4.1, the difference is carried by the label and
 *    border style rather than by hue.
 * 2. **Nothing here is re-derived.** Every value is read from `payload.nutrition`, which the
 *    engine computed and froze at write time. This component does no arithmetic — not even
 *    summing the components, because the stored total is the number the answer cited and a
 *    re-sum could disagree with it.
 * 3. **Excluded foods are shown, not omitted.** A food the model declined contributes nothing
 *    to the total, which makes its absence invisible in the number itself. This is the only
 *    place the user can see that the total is partial.
 * 4. **Estimates are never rendered bare.** A `~` prefix and an explicit confidence label,
 *    every time, so no reading of this pane mistakes an estimate for a measurement.
 */

import { useState } from "react";
import { ChevronRight } from "lucide-react";
import { cn } from "@/lib/utils";

/* The shapes this component reads. Deliberately permissive and locally defined: `payload` is
 * `Record<string, unknown>` at the schema boundary (it is JSONB, and typing it globally would
 * couple the client to every payload the engine may grow), so the narrowing happens here, once,
 * where it is used. */
interface NutritionComponent {
  item?: unknown;
  understood_as?: unknown;
  kind?: unknown;
  qty_g?: unknown;
  qty_basis?: unknown;
  qty_note?: unknown;
  protein_g?: unknown;
  kcal?: unknown;
  range?: Record<string, unknown>;
  assumptions?: unknown;
  confidence_class?: unknown;
  kcal_recomputed?: unknown;
}

export interface NutritionPayload {
  protein_g?: unknown;
  kcal?: unknown;
  estimated?: unknown;
  basis?: unknown;
  confidence_class?: unknown;
  coverage?: { counted?: unknown; excluded?: unknown };
  range?: Record<string, unknown>;
  components?: unknown;
  unresolved?: unknown;
  method?: { model_id?: unknown; prompt_version?: unknown; pipeline?: unknown };
}

const num = (v: unknown): number | null => (typeof v === "number" && !Number.isNaN(v) ? v : null);
const str = (v: unknown): string | null =>
  typeof v === "string" && v.trim() ? v.trim() : null;
const arr = (v: unknown): unknown[] => (Array.isArray(v) ? v : []);

/** `[low, high]` for a macro, when the model gave one. */
function band(range: Record<string, unknown> | undefined, key: string): [number, number] | null {
  const pair = range?.[key];
  if (!Array.isArray(pair) || pair.length !== 2) return null;
  const [low, high] = [num(pair[0]), num(pair[1])];
  return low === null || high === null ? null : [low, high];
}

/**
 * Reads `payload.nutrition` off a memory row, or null when there is none.
 *
 * A meal with no nutrition is a real state, not an error: the estimate call can fail, and the
 * meal commits regardless (the fact the user reported is never held hostage to a derived
 * value). Callers render nothing rather than an empty frame.
 */
export function readNutrition(payload: Record<string, unknown>): NutritionPayload | null {
  const nutrition = payload["nutrition"];
  if (!nutrition || typeof nutrition !== "object" || Array.isArray(nutrition)) return null;
  return nutrition as NutritionPayload;
}

/** Whether a stored nutrition value was estimated rather than stated by the user. */
export function isEstimated(nutrition: NutritionPayload): boolean {
  return nutrition.basis !== "user_stated" && nutrition.estimated !== false;
}

const CONFIDENCE_TITLE: Record<string, string> = {
  high: "quantity stated by you, and a food the model knows well",
  medium: "either the portion was assumed, or it is a prepared dish",
  low: "an assumed portion of a prepared dish — treat as a rough figure",
};

function ConfidenceTag({ value }: { value: string }) {
  return (
    <span
      title={CONFIDENCE_TITLE[value] ?? "confidence in this estimate"}
      className={cn(
        "rounded-xs px-1.5 py-0.5 font-mono text-micro uppercase tracking-[0.08em]",
        // Fill vs outline, never hue alone — identical in grayscale (DESIGN.md §6.4).
        value === "high" && "bg-surface-3 text-muted-foreground",
        value === "medium" && "border border-border text-muted-foreground",
        value === "low" && "border border-dashed border-border text-faint",
      )}
    >
      {value}
    </span>
  );
}

function QuantityTag({ basis }: { basis: string }) {
  const stated = basis === "stated";
  return (
    <span
      title={
        stated
          ? "you gave this quantity"
          : "the model assumed this portion — you did not state it"
      }
      className={cn(
        "rounded-xs px-1.5 py-0.5 font-mono text-micro uppercase tracking-[0.08em]",
        stated
          ? "bg-surface-3 text-muted-foreground"
          : "border border-dashed border-border text-faint",
      )}
    >
      {stated ? "stated" : "estimated"}
    </span>
  );
}

function ComponentRow({ component }: { component: NutritionComponent }) {
  const item = str(component.item) ?? "unnamed";
  const protein = num(component.protein_g);
  const qty = num(component.qty_g);
  const basis = str(component.qty_basis) ?? "ai_estimated";
  const confidence = str(component.confidence_class);
  const proteinBand = band(component.range, "protein_g");
  const assumptions = arr(component.assumptions).map(str).filter(Boolean) as string[];
  const understood = str(component.understood_as);

  return (
    <li className="border-t border-border py-2 first:border-t-0">
      <div className="flex items-baseline justify-between gap-3">
        <span className="text-dense text-foreground">{item}</span>
        <span className="shrink-0 font-mono text-meta tabular-nums text-muted-foreground">
          {protein === null ? "—" : `${basis === "stated" ? "" : "~"}${protein} g protein`}
        </span>
      </div>

      {understood && (
        <p className="mt-0.5 text-meta text-muted-foreground">understood as {understood}</p>
      )}

      <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
        <QuantityTag basis={basis} />
        {qty !== null && (
          <span className="font-mono text-micro tabular-nums text-faint">{qty} g</span>
        )}
        {confidence && <ConfidenceTag value={confidence} />}
        {proteinBand && (
          <span
            title="the model's own uncertainty band"
            className="font-mono text-micro tabular-nums text-faint"
          >
            range {proteinBand[0]}–{proteinBand[1]} g
          </span>
        )}
      </div>

      {str(component.qty_note) && (
        <p className="mt-1 text-meta text-faint">{str(component.qty_note)}</p>
      )}
      {assumptions.length > 0 && (
        <p className="mt-1 text-meta text-faint">assumes {assumptions.join(" · ")}</p>
      )}
    </li>
  );
}

export interface NutritionDerivationProps {
  nutrition: NutritionPayload;
}

export function NutritionDerivation({ nutrition }: NutritionDerivationProps) {
  const [isOpen, setOpen] = useState(false);

  const components = arr(nutrition.components) as NutritionComponent[];
  const unresolved = arr(nutrition.unresolved) as { item?: unknown; reason?: unknown }[];
  const protein = num(nutrition.protein_g);
  const estimated = isEstimated(nutrition);
  const confidence = str(nutrition.confidence_class);
  const proteinBand = band(nutrition.range, "protein_g");
  const excluded = num(nutrition.coverage?.excluded) ?? unresolved.length;
  const modelId = str(nutrition.method?.model_id);

  return (
    <div className="mt-2 rounded-md border border-border bg-surface-2">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={isOpen}
        className="flex w-full items-baseline gap-2 px-2.5 py-2 text-left"
      >
        <ChevronRight
          className={cn(
            "size-3 shrink-0 self-center transition-transform duration-180 ease-out",
            isOpen && "rotate-90",
          )}
          strokeWidth={1.5}
          aria-hidden="true"
        />
        <span className="font-mono text-micro uppercase tracking-[0.08em] text-faint">
          nutrition
        </span>
        <span className="ml-auto font-mono text-meta tabular-nums text-muted-foreground">
          {protein === null ? "not estimated" : `${estimated ? "~" : ""}${protein} g protein`}
        </span>
      </button>

      {isOpen && (
        <div className="border-t border-border px-2.5 py-2">
          <div className="flex flex-wrap items-center gap-1.5">
            {confidence && <ConfidenceTag value={confidence} />}
            {proteinBand && (
              <span className="font-mono text-micro tabular-nums text-faint">
                total range {proteinBand[0]}–{proteinBand[1]} g
              </span>
            )}
            <span className="font-mono text-micro text-faint">
              {components.length} counted
              {excluded > 0 ? ` · ${excluded} excluded` : ""}
            </span>
          </div>

          {components.length > 0 && (
            <ul className="mt-2">
              {components.map((component, i) => (
                <ComponentRow key={`${str(component.item) ?? "item"}-${i}`} component={component} />
              ))}
            </ul>
          )}

          {/* The honest gap. Shown even though — especially though — it is missing from the
              total, because a partial number is otherwise indistinguishable from a complete one. */}
          {unresolved.length > 0 && (
            <div className="mt-2 rounded-xs border border-dashed border-border px-2 py-1.5">
              <p className="font-mono text-micro uppercase tracking-[0.08em] text-faint">
                not included in the total
              </p>
              <ul className="mt-1">
                {unresolved.map((food, i) => (
                  <li key={`${str(food.item) ?? "food"}-${i}`} className="text-meta text-muted-foreground">
                    {str(food.item) ?? "unnamed"}
                    <span className="text-faint"> — {str(food.reason) ?? "not estimated"}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* The bottom of this particular glass box: which model produced the estimate. A
              nutrition value is provider- and prompt-dependent, so attribution is part of it. */}
          {modelId && (
            <p className="mt-2 font-mono text-micro text-faint">estimated by {modelId}</p>
          )}
        </div>
      )}
    </div>
  );
}
