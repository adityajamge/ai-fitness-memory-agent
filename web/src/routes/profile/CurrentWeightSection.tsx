/**
 * Current weight — styled like logging a value, not editing a settings field, because it
 * writes a new `weight` memory rather than a mutated profile column (ADR-17.2). `memo`'d for
 * the same reason as `IdentitySection` — see that file's note.
 */

import { memo } from "react";
import { Button } from "@/components/ui/Button";
import { kgToLb } from "@/lib/targets";

export interface CurrentWeightSectionProps {
  currentWeightKg: number | null;
  units: "metric" | "imperial";
  newWeightInput: string;
  onNewWeightInputChange: (v: string) => void;
  onLogWeight: () => void;
  isLogging: boolean;
}

export const CurrentWeightSection = memo(function CurrentWeightSection({
  currentWeightKg,
  units,
  newWeightInput,
  onNewWeightInputChange,
  onLogWeight,
  isLogging,
}: CurrentWeightSectionProps) {
  return (
    <section className="flex flex-col gap-2 border-t border-border pt-6">
      <h2 className="text-dense font-medium text-foreground">Current weight</h2>
      <p className="text-meta text-faint">
        {currentWeightKg != null
          ? `Last logged: ${
              units === "imperial"
                ? `${Math.round(kgToLb(currentWeightKg) * 10) / 10} lb`
                : `${currentWeightKg} kg`
            }`
          : "Nothing logged yet"}
      </p>
      <div className="flex gap-2">
        <input
          type="number"
          inputMode="decimal"
          value={newWeightInput}
          onChange={(e) => onNewWeightInputChange(e.target.value)}
          placeholder={`log a new weight (${units === "metric" ? "kg" : "lb"})`}
          className="h-10 w-full rounded-sm border border-border bg-surface px-3 text-body text-foreground placeholder:text-faint focus:border-border-strong focus:outline-none"
        />
        <Button type="button" variant="secondary" onClick={onLogWeight} isLoading={isLogging}>
          Log
        </Button>
      </div>
    </section>
  );
});
