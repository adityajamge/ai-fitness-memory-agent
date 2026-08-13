/**
 * Nutrition targets — the live suggestion (client-side mirror of `compute_targets`) plus the
 * two editable target fields. `memo`'d, see `IdentitySection`. `preview` is already
 * `useMemo`'d by the caller against only its real inputs (weight/height/DOB/sex/activity/goal),
 * so it stays referentially stable while this section's own fields — or any other section's —
 * are being typed into, which is what lets the memo below actually skip re-rendering then.
 */

import { memo } from "react";
import { Field } from "@/components/ui/Field";
import type { TargetPreview } from "@/lib/targets";

export interface NutritionTargetsSectionProps {
  preview: TargetPreview | null;
  proteinTargetInput: string;
  onProteinTargetInputChange: (v: string) => void;
  calorieTargetInput: string;
  onCalorieTargetInputChange: (v: string) => void;
  targetsAreCustom: boolean;
}

export const NutritionTargetsSection = memo(function NutritionTargetsSection({
  preview,
  proteinTargetInput,
  onProteinTargetInputChange,
  calorieTargetInput,
  onCalorieTargetInputChange,
  targetsAreCustom,
}: NutritionTargetsSectionProps) {
  return (
    <section className="flex flex-col gap-4 border-t border-border pt-6">
      <h2 className="text-dense font-medium text-foreground">Nutrition targets</h2>
      {preview && (
        <div className="rounded-sm border border-border bg-surface px-4 py-3">
          <p className="font-mono text-body text-signal">
            ~{preview.proteinG}g protein · ~{preview.calorieKcal} kcal
          </p>
          <details className="mt-1">
            <summary className="cursor-pointer text-meta text-muted-foreground">
              how this is calculated
            </summary>
            <p className="mt-1 text-meta text-faint">{preview.basis}</p>
          </details>
        </div>
      )}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <Field
          label="Protein target (g/day)"
          type="number"
          inputMode="decimal"
          hint={targetsAreCustom ? "custom — no longer auto-updated" : "auto-computed from your profile"}
          value={proteinTargetInput}
          onChange={(e) => onProteinTargetInputChange(e.target.value)}
        />
        <Field
          label="Calorie target (kcal/day)"
          type="number"
          inputMode="decimal"
          value={calorieTargetInput}
          onChange={(e) => onCalorieTargetInputChange(e.target.value)}
        />
      </div>
    </section>
  );
});
