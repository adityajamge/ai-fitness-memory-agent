/**
 * Profile & goals — DESIGN.md §6.19, ADR-17. Amended 2026-08-12 (§16 Decisions Log): desktop
 * reaches this over the app as a dialog (`ProfileSettingsDialog`), mobile as this full page —
 * `ProfileSettingsContent` below is the shared body both render, so the two surfaces cannot drift.
 *
 * **Composed of memoized sections** (fixed 2026-08-13, `routes/profile/*Section.tsx`): this
 * component owns every field's state — it has to, `preview` and `handleSave` both need values
 * that span sections — but that means *something* has to stop a keystroke in Identity from
 * re-rendering Goals, Dietary preferences, and Injuries too. `Conversation`/`EvidencePane`
 * already solve the identical problem for `AppScreen` (turn history and the evidence trace sit
 * right next to the composer's `draft` state and don't re-render on a keystroke because neither
 * one's props depend on it); the sections here are that same split. Every callback handed to a
 * memoized section is either a bare `useState` setter (already stable) or explicitly
 * `useCallback`'d against only the state it actually reads — `addAllergy`/`removeAllergy`
 * (Dietary) and `handleLogWeight` (Current weight) — so an unrelated keystroke leaves their
 * identity, and therefore that section's props, untouched.
 *
 * Sections: Identity, Current weight, Goals, Nutrition targets, Dietary preferences &
 * allergies, Injuries/notes, and a closing explainer — the user-facing statement of the
 * historical-integrity rule ADR-17 enforces structurally (`profile_change` history: changing a
 * target here only affects targets going forward).
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate } from "react-router";
import { useProfile, useUpdateProfile } from "@/api/queries";
import { Logo } from "@/components/Logo";
import { Button } from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/Skeleton";
import {
  cmToIn,
  inToCm,
  kgToLb,
  lbToKg,
  previewTargets,
  type ActivityLevel,
  type PrimaryGoal,
  type Sex,
} from "@/lib/targets";
import { CurrentWeightSection } from "./profile/CurrentWeightSection";
import { DietarySection } from "./profile/DietarySection";
import { GoalsSection } from "./profile/GoalsSection";
import { IdentitySection } from "./profile/IdentitySection";
import { InjuriesSection } from "./profile/InjuriesSection";
import { NutritionTargetsSection } from "./profile/NutritionTargetsSection";

/**
 * The form itself — no page chrome, so it can sit inside either a full page (`ProfileSettings`,
 * mobile) or a dialog popup (`ProfileSettingsDialog`, desktop) with the exact same behavior.
 */
export function ProfileSettingsContent() {
  const profile = useProfile();
  // Destructured rather than kept as one `update` object: `mutateAsync` is TanStack Query's
  // stable function reference (unlike the mutation object itself, which gets a new reference on
  // most renders), and that stability is what lets `handleLogWeight` below be `useCallback`'d
  // against only the state it actually needs.
  const { mutateAsync: saveProfile, isPending: isSaving } = useUpdateProfile();

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
  // Guards the seed effect below so it runs exactly once, ever — see that effect's comment for
  // why a plain `[profile.data]` dependency array is not enough (fixed 2026-08-13).
  const seeded = useRef(false);

  // Seed local state from the loaded profile exactly once — after that the form is the source
  // of truth for what the user is editing, the same "seed once, then local wins" pattern
  // AppScreen uses for conversation history (`seeded.current`, not just a dependency array).
  //
  // **That guard is load-bearing, not decorative.** `profile.data` gets a *new object
  // reference* on every successful mutation this screen makes, including a partial one:
  // `handleLogWeight` only changes `weight_kg`, but its response still replaces the whole
  // cached profile object. Without `seeded.current`, this effect would re-fire on that response
  // and overwrite every *other* field back to its last-saved server value — silently discarding
  // whatever the user had typed into Name, Height, Goals, etc. but not yet saved. Caught by a
  // smoke test that logged a weight partway through filling the rest of the form.
  useEffect(() => {
    if (seeded.current || !profile.data) return;
    seeded.current = true;
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

  // `useCallback`, not a plain function: this is `DietarySection`'s prop, and a fresh reference
  // on every render (any keystroke, anywhere in the form) would defeat that section's memo. Its
  // identity legitimately changes when `allergyDraft`/`allergies` change — which is exactly when
  // Dietary is already re-rendering for its own reasons, so that's free.
  const addAllergy = useCallback(() => {
    const value = allergyDraft.trim();
    if (!value || allergies.includes(value)) {
      setAllergyDraft("");
      return;
    }
    setAllergies((prev) => [...prev, value]);
    setAllergyDraft("");
  }, [allergyDraft, allergies]);

  // Fully stable (functional update, no closed-over state) — never forces Dietary to re-render.
  const removeAllergy = useCallback((item: string) => {
    setAllergies((prev) => prev.filter((a) => a !== item));
  }, []);

  async function handleSave() {
    setSaveError(undefined);
    setSaved(false);
    // Built via conditional spreads, never `field ?? undefined`: under
    // `exactOptionalPropertyTypes` an explicit `undefined` is not the same as an absent key
    // (client.ts's `request` helper carries the same note), and `ProfileEditInput`'s partial-
    // update semantics depend on only-the-fields-you-mean-to-change being present at all.
    try {
      await saveProfile({
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

  // `useCallback`, not a plain function: `CurrentWeightSection`'s prop — see `addAllergy` above
  // for why. Depends only on `newWeightInput`/`units` (both that section's own props already)
  // and `saveProfile` (TanStack Query's stable `mutateAsync`), never on unrelated form state.
  const handleLogWeight = useCallback(async () => {
    if (!newWeightInput) return;
    setSaveError(undefined);
    const kg = units === "imperial" ? lbToKg(Number(newWeightInput)) : Number(newWeightInput);
    try {
      await saveProfile({ weight_kg: Math.round(kg * 10) / 10 });
      setNewWeightInput("");
      setSaved(true);
    } catch {
      setSaveError("couldn't reach the server");
    }
  }, [newWeightInput, units, saveProfile]);

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
      <IdentitySection
        displayName={displayName}
        onDisplayNameChange={setDisplayName}
        dateOfBirth={dateOfBirth}
        onDateOfBirthChange={setDateOfBirth}
        sex={sex}
        onSexChange={setSex}
        units={units}
        onUnitsChange={setUnits}
        heightInput={heightInput}
        onHeightInputChange={setHeightInput}
      />

      <CurrentWeightSection
        currentWeightKg={currentWeightKg}
        units={units}
        newWeightInput={newWeightInput}
        onNewWeightInputChange={setNewWeightInput}
        onLogWeight={handleLogWeight}
        isLogging={isSaving}
      />

      <GoalsSection
        primaryGoal={primaryGoal}
        onPrimaryGoalChange={setPrimaryGoal}
        activityLevel={activityLevel}
        onActivityLevelChange={setActivityLevel}
        units={units}
        targetWeightInput={targetWeightInput}
        onTargetWeightInputChange={setTargetWeightInput}
      />

      <NutritionTargetsSection
        preview={preview}
        proteinTargetInput={proteinTargetInput}
        onProteinTargetInputChange={setProteinTargetInput}
        calorieTargetInput={calorieTargetInput}
        onCalorieTargetInputChange={setCalorieTargetInput}
        targetsAreCustom={Boolean(profile.data?.targets_are_custom)}
      />

      <DietarySection
        dietaryPreference={dietaryPreference}
        onDietaryPreferenceChange={setDietaryPreference}
        allergyDraft={allergyDraft}
        onAllergyDraftChange={setAllergyDraft}
        allergies={allergies}
        onAddAllergy={addAllergy}
        onRemoveAllergy={removeAllergy}
      />

      <InjuriesSection injuries={injuries} onInjuriesChange={setInjuries} />

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
        <Button type="button" variant="primary" size="lg" isLoading={isSaving} onClick={handleSave}>
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
