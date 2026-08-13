/** Goals — primary goal, activity level, target weight (§6.19). `memo`'d, see `IdentitySection`. */

import { memo } from "react";
import { Field } from "@/components/ui/Field";
import { ToggleGroup } from "@/components/ui/ToggleGroup";
import {
  ACTIVITY_LEVELS,
  PRIMARY_GOALS,
  activityLabel,
  goalLabel,
  type ActivityLevel,
  type PrimaryGoal,
} from "@/lib/targets";
import { SectionLabel } from "./SectionLabel";

export interface GoalsSectionProps {
  primaryGoal: PrimaryGoal | null;
  onPrimaryGoalChange: (v: PrimaryGoal) => void;
  activityLevel: ActivityLevel | null;
  onActivityLevelChange: (v: ActivityLevel) => void;
  units: "metric" | "imperial";
  targetWeightInput: string;
  onTargetWeightInputChange: (v: string) => void;
}

export const GoalsSection = memo(function GoalsSection({
  primaryGoal,
  onPrimaryGoalChange,
  activityLevel,
  onActivityLevelChange,
  units,
  targetWeightInput,
  onTargetWeightInputChange,
}: GoalsSectionProps) {
  return (
    <section className="flex flex-col gap-4 border-t border-border pt-6">
      <h2 className="text-dense font-medium text-foreground">Goals</h2>
      <div className="flex flex-col gap-1.5">
        <SectionLabel>Primary goal</SectionLabel>
        <ToggleGroup<PrimaryGoal>
          options={PRIMARY_GOALS}
          value={primaryGoal}
          onChange={onPrimaryGoalChange}
          labelOf={goalLabel}
        />
      </div>
      <div className="flex flex-col gap-1.5">
        <SectionLabel>Activity level</SectionLabel>
        <ToggleGroup<ActivityLevel>
          options={ACTIVITY_LEVELS}
          value={activityLevel}
          onChange={onActivityLevelChange}
          labelOf={activityLabel}
        />
      </div>
      <Field
        label={`Target weight (${units === "metric" ? "kg" : "lb"})`}
        type="number"
        inputMode="decimal"
        value={targetWeightInput}
        onChange={(e) => onTargetWeightInputChange(e.target.value)}
      />
    </section>
  );
});
