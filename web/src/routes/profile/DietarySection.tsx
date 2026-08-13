/** Dietary preferences & allergies (§6.19). `memo`'d, see `IdentitySection`. */

import { memo } from "react";
import { Button } from "@/components/ui/Button";
import { Field } from "@/components/ui/Field";
import { SectionLabel } from "./SectionLabel";

export interface DietarySectionProps {
  dietaryPreference: string;
  onDietaryPreferenceChange: (v: string) => void;
  allergyDraft: string;
  onAllergyDraftChange: (v: string) => void;
  allergies: string[];
  onAddAllergy: () => void;
  onRemoveAllergy: (item: string) => void;
}

export const DietarySection = memo(function DietarySection({
  dietaryPreference,
  onDietaryPreferenceChange,
  allergyDraft,
  onAllergyDraftChange,
  allergies,
  onAddAllergy,
  onRemoveAllergy,
}: DietarySectionProps) {
  return (
    <section className="flex flex-col gap-4 border-t border-border pt-6">
      <h2 className="text-dense font-medium text-foreground">Dietary preferences & allergies</h2>
      <Field
        label="Dietary preference"
        placeholder="vegetarian, vegan, omnivore…"
        value={dietaryPreference}
        onChange={(e) => onDietaryPreferenceChange(e.target.value)}
      />
      <div className="flex flex-col gap-1.5">
        <SectionLabel>Allergies / restrictions</SectionLabel>
        <div className="flex gap-2">
          <input
            value={allergyDraft}
            onChange={(e) => onAllergyDraftChange(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                onAddAllergy();
              }
            }}
            placeholder="peanuts, shellfish…"
            className="h-10 w-full rounded-sm border border-border bg-surface px-3 text-body text-foreground placeholder:text-faint focus:border-border-strong focus:outline-none"
          />
          <Button type="button" variant="secondary" onClick={onAddAllergy}>
            Add
          </Button>
        </div>
        {allergies.length > 0 && (
          <div className="mt-1 flex flex-wrap gap-2">
            {allergies.map((item) => (
              <button
                key={item}
                type="button"
                onClick={() => onRemoveAllergy(item)}
                className="rounded-sm border border-border bg-surface-2 px-2 py-1 text-meta text-foreground transition-colors duration-120 hover:border-invalid hover:text-invalid"
              >
                {item} ×
              </button>
            ))}
          </div>
        )}
      </div>
    </section>
  );
});
