/**
 * Profile & goals — DESIGN.md §6.19, ADR-17. Amended 2026-08-12 (§16 Decisions Log): desktop
 * reaches this over the app as a dialog (`ProfileSettingsDialog`), mobile as this full page —
 * `ProfileSettingsContent` below is the shared body both render, so the two surfaces cannot drift.
 *
 * Sections: Identity, Goals, Nutrition targets, Dietary preferences & allergies, Injuries/notes,
 * and a closing explainer — the user-facing statement of the historical-integrity rule ADR-17
 * enforces structurally (`profile_change` history: changing a target here only affects targets
 * going forward).
 */

import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Link, useNavigate } from "react-router";
import { useProfile, useUpdateProfile } from "@/api/queries";
import { Logo } from "@/components/Logo";
import { Button } from "@/components/ui/Button";
import { Field } from "@/components/ui/Field";
import { Skeleton } from "@/components/ui/Skeleton";
import { ToggleGroup } from "@/components/ui/ToggleGroup";
import {
  ACTIVITY_LEVELS,
  PRIMARY_GOALS,
  cmToIn,
  inToCm,
  kgToLb,
  lbToKg,
  previewTargets,
  type ActivityLevel,
  type PrimaryGoal,
  type Sex,
} from "@/lib/targets";

const GOAL_LABEL: Record<PrimaryGoal, string> = {
  lose_fat: "Lose fat",
  build_muscle: "Build muscle",
  recomp: "Recomp",
  maintain: "Maintain",
  general_health: "General health",
};

const ACTIVITY_LABEL: Record<ActivityLevel, string> = {
  sedentary: "Sedentary",
  light: "Lightly active",
  moderate: "Moderately active",
  very_active: "Very active",
  athlete: "Athlete",
};

function SectionLabel({ children }: { children: ReactNode }) {
  return (
    <span className="font-mono text-micro uppercase tracking-[0.08em] text-faint">
      {children}
    </span>
  );
}

/**
 * The form itself — no page chrome, so it can sit inside either a full page (`ProfileSettings`,
 * mobile) or a dialog popup (`ProfileSettingsDialog`, desktop) with the exact same behavior.
 */
export function ProfileSettingsContent() {
  const profile = useProfile();
  const update = useUpdateProfile();

  const [displayName, setDisplayName] = useState("");
  const [dateOfBirth, setDateOfBirth] = useState("");
  const [sex, setSex] = useState<Sex | null>(null);
  const [units, setUnits] = useState<"metric" | "imperial">("metric");
  const [heightInput, setHeightInput] = useState("");
  const [primaryGoal, setPrimaryGoal] = useState<PrimaryGoal | null>(null);
  const [activityLevel, setActivityLevel] = useState<ActivityLevel | null>(null);
  const [targetWeightInput, setTargetWeightInput] = useState("");
  const [dietaryPreference, setDietaryPreference] = useState("");
  const [allergyDraft, setAllergyDraft] = useState("");
  const [allergies, setAllergies] = useState<string[]>([]);
  const [injuries, setInjuries] = useState("");
  const [proteinTargetInput, setProteinTargetInput] = useState("");
  const [calorieTargetInput, setCalorieTargetInput] = useState("");
  const [newWeightInput, setNewWeightInput] = useState("");
  const [saveError, setSaveError] = useState<string>();
  const [saved, setSaved] = useState(false);

  // Seed local state from the loaded profile exactly once — after that the form is the
  // source of truth for what the user is editing, the same "seed once, then local wins"
  // pattern AppScreen uses for conversation history.
  useEffect(() => {
    if (!profile.data) return;
    setDisplayName(profile.data.display_name ?? "");
    setDateOfBirth(profile.data.date_of_birth ?? "");
    setSex(profile.data.sex);
    setUnits(profile.data.units);
    setHeightInput(
      profile.data.height_cm != null
        ? String(
            profile.data.units === "imperial"
              ? Math.round(cmToIn(profile.data.height_cm) * 10) / 10
              : profile.data.height_cm,
          )
        : "",
    );
    setPrimaryGoal((profile.data.primary_goal as PrimaryGoal | null) ?? null);
    setActivityLevel((profile.data.activity_level as ActivityLevel | null) ?? null);
    setTargetWeightInput(
      profile.data.target_weight_kg != null
        ? String(
            profile.data.units === "imperial"
              ? Math.round(kgToLb(profile.data.target_weight_kg) * 10) / 10
              : profile.data.target_weight_kg,
          )
        : "",
    );
    setDietaryPreference(profile.data.dietary_preference ?? "");
    setAllergies(profile.data.allergies);
    setInjuries(profile.data.injuries ?? "");
    setProteinTargetInput(
      profile.data.protein_target_g != null ? String(profile.data.protein_target_g) : "",
    );
    setCalorieTargetInput(
      profile.data.calorie_target_kcal != null ? String(profile.data.calorie_target_kcal) : "",
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps -- seed once per successful load
  }, [profile.data]);

  const heightCm = useMemo(() => {
    const n = Number(heightInput);
    if (!heightInput || Number.isNaN(n)) return null;
    return units === "imperial" ? inToCm(n) : n;
  }, [heightInput, units]);

  const currentWeightKg = profile.data?.current_weight_kg ?? null;

  const preview = useMemo(
    () =>
      previewTargets({
        weightKg: currentWeightKg,
        heightCm,
        dateOfBirth: dateOfBirth || null,
        sex,
        activityLevel,
        primaryGoal,
      }),
    [currentWeightKg, heightCm, dateOfBirth, sex, activityLevel, primaryGoal],
  );

  function addAllergy() {
    const value = allergyDraft.trim();
    if (!value || allergies.includes(value)) {
      setAllergyDraft("");
      return;
    }
    setAllergies((prev) => [...prev, value]);
    setAllergyDraft("");
  }

  async function handleSave() {
    setSaveError(undefined);
    setSaved(false);
    // Built via conditional spreads, never `field ?? undefined`: under
    // `exactOptionalPropertyTypes` an explicit `undefined` is not the same as an absent key
    // (client.ts's `request` helper carries the same note), and `ProfileEditInput`'s partial-
    // update semantics depend on only-the-fields-you-mean-to-change being present at all.
    try {
      await update.mutateAsync({
        ...(displayName ? { display_name: displayName } : {}),
        ...(dateOfBirth ? { date_of_birth: dateOfBirth } : {}),
        ...(sex ? { sex } : {}),
        ...(heightCm !== null ? { height_cm: heightCm } : {}),
        units,
        ...(primaryGoal ? { primary_goal: primaryGoal } : {}),
        ...(activityLevel ? { activity_level: activityLevel } : {}),
        ...(targetWeightInput
          ? {
              target_weight_kg:
                units === "imperial"
                  ? lbToKg(Number(targetWeightInput))
                  : Number(targetWeightInput),
            }
          : {}),
        ...(dietaryPreference ? { dietary_preference: dietaryPreference } : {}),
        allergies,
        ...(injuries ? { injuries } : {}),
        ...(proteinTargetInput ? { protein_target_g: Number(proteinTargetInput) } : {}),
        ...(calorieTargetInput ? { calorie_target_kcal: Number(calorieTargetInput) } : {}),
      });
      setSaved(true);
    } catch {
      setSaveError("couldn't reach the server");
    }
  }

  async function handleLogWeight() {
    if (!newWeightInput) return;
    setSaveError(undefined);
    const kg = units === "imperial" ? lbToKg(Number(newWeightInput)) : Number(newWeightInput);
    try {
      await update.mutateAsync({ weight_kg: Math.round(kg * 10) / 10 });
      setNewWeightInput("");
      setSaved(true);
    } catch {
      setSaveError("couldn't reach the server");
    }
  }

  if (profile.isPending) {
    return (
      <div className="flex flex-col gap-4">
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-10 w-2/3" />
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-8">
      {/* Identity */}
      <section className="flex flex-col gap-4">
        <h2 className="text-dense font-medium text-foreground">Identity</h2>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Field label="Name" value={displayName} onChange={(e) => setDisplayName(e.target.value)} />
          <Field
            label="Date of birth"
            type="date"
            value={dateOfBirth}
            onChange={(e) => setDateOfBirth(e.target.value)}
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <SectionLabel>Sex</SectionLabel>
          <ToggleGroup<Sex>
            options={["male", "female"]}
            value={sex}
            onChange={setSex}
            labelOf={(v) => (v === "male" ? "Male" : "Female")}
          />
        </div>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <div className="flex flex-col gap-1.5">
            <SectionLabel>Units</SectionLabel>
            <ToggleGroup<"metric" | "imperial">
              options={["metric", "imperial"]}
              value={units}
              onChange={setUnits}
              labelOf={(v) => (v === "metric" ? "kg / cm" : "lb / in")}
            />
          </div>
          <Field
            label={`Height (${units === "metric" ? "cm" : "in"})`}
            type="number"
            inputMode="decimal"
            value={heightInput}
            onChange={(e) => setHeightInput(e.target.value)}
          />
        </div>
      </section>

      {/* Current weight — deliberately styled like logging a value, not editing a
          settings field: it writes a new `weight` memory, never a profile column
          (ADR-17.2). */}
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
            onChange={(e) => setNewWeightInput(e.target.value)}
            placeholder={`log a new weight (${units === "metric" ? "kg" : "lb"})`}
            className="h-10 w-full rounded-sm border border-border bg-surface px-3 text-body text-foreground placeholder:text-faint focus:border-border-strong focus:outline-none"
          />
          <Button
            type="button"
            variant="secondary"
            onClick={handleLogWeight}
            isLoading={update.isPending}
          >
            Log
          </Button>
        </div>
      </section>

      {/* Goals */}
      <section className="flex flex-col gap-4 border-t border-border pt-6">
        <h2 className="text-dense font-medium text-foreground">Goals</h2>
        <div className="flex flex-col gap-1.5">
          <SectionLabel>Primary goal</SectionLabel>
          <ToggleGroup<PrimaryGoal>
            options={PRIMARY_GOALS}
            value={primaryGoal}
            onChange={setPrimaryGoal}
            labelOf={(v) => GOAL_LABEL[v]}
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <SectionLabel>Activity level</SectionLabel>
          <ToggleGroup<ActivityLevel>
            options={ACTIVITY_LEVELS}
            value={activityLevel}
            onChange={setActivityLevel}
            labelOf={(v) => ACTIVITY_LABEL[v]}
          />
        </div>
        <Field
          label={`Target weight (${units === "metric" ? "kg" : "lb"})`}
          type="number"
          inputMode="decimal"
          value={targetWeightInput}
          onChange={(e) => setTargetWeightInput(e.target.value)}
        />
      </section>

      {/* Nutrition targets */}
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
            hint={
              profile.data?.targets_are_custom
                ? "custom — no longer auto-updated"
                : "auto-computed from your profile"
            }
            value={proteinTargetInput}
            onChange={(e) => setProteinTargetInput(e.target.value)}
          />
          <Field
            label="Calorie target (kcal/day)"
            type="number"
            inputMode="decimal"
            value={calorieTargetInput}
            onChange={(e) => setCalorieTargetInput(e.target.value)}
          />
        </div>
      </section>

      {/* Dietary preferences & allergies */}
      <section className="flex flex-col gap-4 border-t border-border pt-6">
        <h2 className="text-dense font-medium text-foreground">Dietary preferences & allergies</h2>
        <Field
          label="Dietary preference"
          placeholder="vegetarian, vegan, omnivore…"
          value={dietaryPreference}
          onChange={(e) => setDietaryPreference(e.target.value)}
        />
        <div className="flex flex-col gap-1.5">
          <SectionLabel>Allergies / restrictions</SectionLabel>
          <div className="flex gap-2">
            <input
              value={allergyDraft}
              onChange={(e) => setAllergyDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  addAllergy();
                }
              }}
              placeholder="peanuts, shellfish…"
              className="h-10 w-full rounded-sm border border-border bg-surface px-3 text-body text-foreground placeholder:text-faint focus:border-border-strong focus:outline-none"
            />
            <Button type="button" variant="secondary" onClick={addAllergy}>
              Add
            </Button>
          </div>
          {allergies.length > 0 && (
            <div className="mt-1 flex flex-wrap gap-2">
              {allergies.map((item) => (
                <button
                  key={item}
                  type="button"
                  onClick={() => setAllergies((prev) => prev.filter((a) => a !== item))}
                  className="rounded-sm border border-border bg-surface-2 px-2 py-1 text-meta text-foreground transition-colors duration-120 hover:border-invalid hover:text-invalid"
                >
                  {item} ×
                </button>
              ))}
            </div>
          )}
        </div>
      </section>

      {/* Injuries / notes */}
      <section className="flex flex-col gap-2 border-t border-border pt-6">
        <h2 className="text-dense font-medium text-foreground">Injuries / notes</h2>
        <textarea
          value={injuries}
          onChange={(e) => setInjuries(e.target.value)}
          rows={3}
          placeholder="anything AyuMind should know when talking about training or recovery"
          className="w-full rounded-sm border border-border bg-surface px-3 py-2 text-body text-foreground placeholder:text-faint focus:border-border-strong focus:outline-none"
        />
      </section>

      <p className="border-t border-border pt-6 text-meta text-faint">
        Your protein target uses your weight, activity level, and goal. Changing it here only
        affects targets going forward — past days are judged against the target that was active
        then.
      </p>

      {saveError && (
        <p role="alert" className="text-meta text-invalid">
          {saveError}
        </p>
      )}
      {saved && !saveError && (
        <p className="text-meta text-signal" aria-live="polite">
          Saved.
        </p>
      )}

      <div>
        <Button
          type="button"
          variant="primary"
          size="lg"
          isLoading={update.isPending}
          onClick={handleSave}
        >
          Save changes
        </Button>
      </div>
    </div>
  );
}

/** The full-page surface — reached directly (mobile: the icon navigates here plainly; desktop:
 * a direct link or a page refresh while the dialog is open) rather than as an overlay. */
export function ProfileSettings() {
  const navigate = useNavigate();

  return (
    <main className="relative min-h-dvh overflow-hidden">
      <div className="relative flex min-h-dvh justify-center px-6 py-12 md:px-[max(6vw,48px)]">
        <div className="w-full max-w-[560px]">
          <div className="flex items-center justify-between">
            <Link to="/app" aria-label="Back to AyuMind" className="inline-block">
              <Logo size={20} animated={false} glowStrength={0} />
            </Link>
            <Button variant="ghost" size="sm" onClick={() => navigate("/app")}>
              Back
            </Button>
          </div>

          <h1 className="mt-8 text-h2 text-foreground">Profile & goals</h1>

          <div className="mt-8">
            <ProfileSettingsContent />
          </div>
        </div>
      </div>
    </main>
  );
}
