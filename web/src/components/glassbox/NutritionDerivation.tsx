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
 *    `ai_estimated` is a portion the model chose. They are different *kinds* of fact, and the
 *    difference is spelled out in words next to the amount (`250 g · stated`) — carried by
 *    text, never by hue, so it survives grayscale and a screen reader alike (WCAG 1.4.1).
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

/**
 * Whether the model's reading of a food adds anything over the name the user used.
 *
 * "chicken breast" → "chicken breast, cooked, skinless" is worth a line: it says what was
 * assumed. "dal" → "dal" is noise. Comparing loosely (case, punctuation) rather than exactly,
 * because the near-identical case is the common one.
 */
function addsMeaning(understood: string | null, item: string): boolean {
  if (!understood) return false;
  const norm = (s: string) => s.toLowerCase().replace(/[^a-z0-9]/g, "");
  return norm(understood) !== norm(item);
}

/**
 * One food's contribution.
 *
 * Density is deliberate. The first pass showed the same fact up to three times — a `STATED`
 * tag, a `250 g` chip, and a `user stated 250 g` note all saying one thing — which made a
 * single-food meal look like a form. Each fact now appears once:
 *
 * - the **quantity and how it was known** merge into the heading (`250 g · stated`)
 * - `qty_note` renders **only for an assumed portion**, where it carries the actual assumption;
 *   for a stated quantity it can only ever restate the heading
 * - `understood as` renders only when it differs from what the user wrote
 */
function ComponentRow({ component, showConfidence }: {
  component: NutritionComponent;
  showConfidence: boolean;
}) {
  const item = str(component.item) ?? "unnamed";
  const protein = num(component.protein_g);
  const qty = num(component.qty_g);
  const basis = str(component.qty_basis) ?? "ai_estimated";
  const isStated = basis === "stated";
  const confidence = str(component.confidence_class);
  const proteinBand = band(component.range, "protein_g");
  const assumptions = arr(component.assumptions).map(str).filter(Boolean) as string[];
  const understood = str(component.understood_as);
  // Only meaningful when the model chose the portion; for a stated amount it restates the line
  // directly above it.
  const qtyNote = isStated ? null : str(component.qty_note);

  return (
    <li className="border-t border-border py-2 first:border-t-0">
      <div className="flex items-baseline justify-between gap-3">
        <span className="min-w-0 text-dense text-foreground">
          {item}
          {qty !== null && (
            <span className="ml-1.5 font-mono text-meta tabular-nums text-faint">
              {qty} g · {isStated ? "stated" : "estimated"}
            </span>
          )}
        </span>
        <span className="shrink-0 font-mono text-meta tabular-nums text-muted-foreground">
          {protein === null ? "—" : `${isStated ? "" : "~"}${protein} g`}
          {proteinBand && (
            <span className="text-faint">
              {" "}
              ({proteinBand[0]}–{proteinBand[1]})
            </span>
          )}
        </span>
      </div>

      {addsMeaning(understood, item) && (
        <p className="mt-0.5 text-meta text-muted-foreground">read as {understood}</p>
      )}
      {qtyNote && <p className="mt-0.5 text-meta text-faint">{qtyNote}</p>}
      {assumptions.length > 0 && (
        <p className="mt-0.5 text-meta text-faint">assumes {assumptions.join(" · ")}</p>
      )}
      {/* Per-food confidence is only worth showing when the foods disagree; when they all share
          one class the header already said it once. */}
      {showConfidence && confidence && (
        <div className="mt-1">
          <ConfidenceTag value={confidence} />
        </div>
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
  // When every food shares the meal's confidence class, the header states it once and the
  // per-food tags are pure repetition. They earn their place only when the foods disagree —
  // which is exactly when the reader needs to know *which* food is the weak one.
  const mixedConfidence =
    new Set(components.map((c) => str(c.confidence_class) ?? "")).size > 1;

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
                {proteinBand[0]}–{proteinBand[1]} g
              </span>
            )}
            {/* A count only earns its place once there is more than one food, or something was
                left out. "1 counted" over a one-food meal is a label for a fact the reader can
                already see — and a meal logged before this pipeline existed carries a total and
                no breakdown at all, where "0 counted" would read as "we counted nothing". */}
            {(components.length > 1 || excluded > 0) && (
              <span className="font-mono text-micro text-faint">
                {components.length} counted
                {excluded > 0 ? ` · ${excluded} excluded` : ""}
              </span>
            )}
          </div>

          {/* The honest reading of a pre-pipeline row: a stored estimate whose derivation was
              never recorded, so there is nothing to open. Saying so beats an empty panel. */}
          {components.length === 0 && excluded === 0 && (
            <p className="mt-2 text-meta text-faint">
              This estimate was stored before the engine recorded per-food derivations, so there
              is no breakdown to show.
            </p>
          )}

          {components.length > 0 && (
            <ul className="mt-2">
              {components.map((component, i) => (
                <ComponentRow
                  key={`${str(component.item) ?? "item"}-${i}`}
                  component={component}
                  showConfidence={mixedConfidence}
                />
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
