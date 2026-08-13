/**
 * Identity — name, date of birth, sex, units, height (§6.19).
 *
 * `memo`'d (fixed 2026-08-13, following ToggleGroup): `ProfileSettingsContent` holds every
 * section's state in one component, so without a memo boundary here, typing in an *unrelated*
 * section (Goals, Dietary preferences, Injuries...) would still re-render this one — the exact
 * failure mode `Conversation`/`EvidencePane` already solve for the composer: `AppScreen` holds
 * turn history and the evidence trace right next to `draft`, and neither re-renders on a
 * keystroke because neither's props depend on it. This section (and its siblings) are that same
 * pattern applied to the profile form. For the memo to actually skip anything, every prop here
 * must be referentially stable across an unrelated re-render — `onChange` props are plain
 * `useState` setters (already stable), and `options`/`labelOf` come from `lib/targets.ts`.
 */

import { memo } from "react";
import { Field } from "@/components/ui/Field";
import { ToggleGroup } from "@/components/ui/ToggleGroup";
import { SEX_OPTIONS, UNITS_OPTIONS, sexLabel, unitsLabel, type Sex } from "@/lib/targets";
import { SectionLabel } from "./SectionLabel";

export interface IdentitySectionProps {
  displayName: string;
  onDisplayNameChange: (v: string) => void;
  dateOfBirth: string;
  onDateOfBirthChange: (v: string) => void;
  sex: Sex | null;
  onSexChange: (v: Sex) => void;
  units: "metric" | "imperial";
  onUnitsChange: (v: "metric" | "imperial") => void;
  heightInput: string;
  onHeightInputChange: (v: string) => void;
}

export const IdentitySection = memo(function IdentitySection({
  displayName,
  onDisplayNameChange,
  dateOfBirth,
  onDateOfBirthChange,
  sex,
  onSexChange,
  units,
  onUnitsChange,
  heightInput,
  onHeightInputChange,
}: IdentitySectionProps) {
  return (
    <section className="flex flex-col gap-4">
      <h2 className="text-dense font-medium text-foreground">Identity</h2>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <Field
          label="Name"
          value={displayName}
          onChange={(e) => onDisplayNameChange(e.target.value)}
        />
        <Field
          label="Date of birth"
          type="date"
          value={dateOfBirth}
          onChange={(e) => onDateOfBirthChange(e.target.value)}
        />
      </div>
      <div className="flex flex-col gap-1.5">
        <SectionLabel>Sex</SectionLabel>
        <ToggleGroup<Sex>
          options={SEX_OPTIONS}
          value={sex}
          onChange={onSexChange}
          labelOf={sexLabel}
        />
      </div>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div className="flex flex-col gap-1.5">
          <SectionLabel>Units</SectionLabel>
          <ToggleGroup<"metric" | "imperial">
            options={UNITS_OPTIONS}
            value={units}
            onChange={onUnitsChange}
            labelOf={unitsLabel}
          />
        </div>
        <Field
          label={`Height (${units === "metric" ? "cm" : "in"})`}
          type="number"
          inputMode="decimal"
          value={heightInput}
          onChange={(e) => onHeightInputChange(e.target.value)}
        />
      </div>
    </section>
  );
});
