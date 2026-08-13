/**
 * The profile intake screen — DESIGN.md §6.19, ADR-17.
 *
 * One screen, shown once immediately after signup. Not a wizard: no steps, no progress dots,
 * nothing to dismiss — same anti-tour posture as the guided first turn (§9.1), applied to a
 * different object (collecting facts the engine needs, not explaining the UI). Same visual
 * grammar as `AuthScreen` (§6.17): left-weighted, no card, no border, no shadow.
 */

import { useCallback, useMemo, useState, type FormEvent } from "react";
import { Navigate, useNavigate } from "react-router";
import { useProfile, useSubmitOnboarding } from "@/api/queries";
import { Logo } from "@/components/Logo";
import { Button } from "@/components/ui/Button";
import { Field } from "@/components/ui/Field";
import { Skeleton } from "@/components/ui/Skeleton";
import { ToggleGroup } from "@/components/ui/ToggleGroup";
import {
  ACTIVITY_LEVELS,
  PRIMARY_GOALS,
  SEX_OPTIONS,
  UNITS_OPTIONS,
  activityLabel,
  cmToIn,
  goalLabel,
  inToCm,
  kgToLb,
  lbToKg,
  previewTargets,
  sexLabel,
  unitsLabel,
  type ActivityLevel,
  type PrimaryGoal,
  type Sex,
} from "@/lib/targets";
import { cn } from "@/lib/utils";

export function Onboarding() {
  const navigate = useNavigate();
  const profile = useProfile();
  const submit = useSubmitOnboarding();

  const [displayName, setDisplayName] = useState("");
  const [dateOfBirth, setDateOfBirth] = useState("");
  const [sex, setSex] = useState<Sex | null>(null);
  const [sexSkipped, setSexSkipped] = useState(false);
  // Stable identity for ToggleGroup's memoization to actually take effect (an inline arrow
  // function here would be a new prop value on every render, same reason as SEX_OPTIONS/
  // sexLabel being hoisted to lib/targets.ts).
  const chooseSex = useCallback((v: Sex) => {
    setSex(v);
    setSexSkipped(false);
  }, []);
  const [units, setUnits] = useState<"metric" | "imperial">("metric");
  const [heightInput, setHeightInput] = useState("");
  const [weightInput, setWeightInput] = useState("");
  const [primaryGoal, setPrimaryGoal] = useState<PrimaryGoal | null>(null);
  const [activityLevel, setActivityLevel] = useState<ActivityLevel | null>(null);
  const [showOptional, setShowOptional] = useState(false);
  const [targetWeightInput, setTargetWeightInput] = useState("");
  const [dietaryPreference, setDietaryPreference] = useState("");
  const [allergyDraft, setAllergyDraft] = useState("");
  const [allergies, setAllergies] = useState<string[]>([]);
  const [formError, setFormError] = useState<string>();

  // Already onboarded (a direct visit, a back button) — redirect like a used-up link, per
  // DESIGN.md §6.19, rather than showing the form again.
  const shouldRedirect = profile.data?.has_onboarded === true;

  // `useCallback`, not a plain function: it's ToggleGroup's `onChange`, and a fresh reference
  // on every render (any keystroke, anywhere in the form) would defeat that component's memo.
  const toggleUnits = useCallback(
    (next: "metric" | "imperial") => {
      if (next === units) return;
      setHeightInput((v) => {
        const n = Number(v);
        if (!v || Number.isNaN(n)) return v;
        return String(Math.round((next === "imperial" ? cmToIn(n) : inToCm(n)) * 10) / 10);
      });
      setWeightInput((v) => {
        const n = Number(v);
        if (!v || Number.isNaN(n)) return v;
        return String(Math.round((next === "imperial" ? kgToLb(n) : lbToKg(n)) * 10) / 10);
      });
      setUnits(next);
    },
    [units],
  );

  const heightCm = useMemo(() => {
    const n = Number(heightInput);
    if (!heightInput || Number.isNaN(n)) return null;
    return units === "imperial" ? inToCm(n) : n;
  }, [heightInput, units]);

  const weightKg = useMemo(() => {
    const n = Number(weightInput);
    if (!weightInput || Number.isNaN(n)) return null;
    return units === "imperial" ? lbToKg(n) : n;
  }, [weightInput, units]);

  const preview = useMemo(
    () =>
      previewTargets({
        weightKg,
        heightCm,
        dateOfBirth: dateOfBirth || null,
        sex: sexSkipped ? null : sex,
        activityLevel,
        primaryGoal,
      }),
    [weightKg, heightCm, dateOfBirth, sex, sexSkipped, activityLevel, primaryGoal],
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

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setFormError(undefined);
    if (!displayName || !dateOfBirth || heightCm === null || weightKg === null || !primaryGoal || !activityLevel) {
      setFormError("fill in the required fields, or skip for now");
      return;
    }
    try {
      await submit.mutateAsync({
        display_name: displayName,
        date_of_birth: dateOfBirth,
        ...(sexSkipped ? {} : sex ? { sex } : {}),
        height_cm: Math.round(heightCm * 10) / 10,
        weight_kg: Math.round(weightKg * 10) / 10,
        primary_goal: primaryGoal,
        activity_level: activityLevel,
        units,
        ...(targetWeightInput ? { target_weight_kg: Number(targetWeightInput) } : {}),
        ...(dietaryPreference ? { dietary_preference: dietaryPreference } : {}),
        ...(allergies.length ? { allergies } : {}),
      });
      navigate("/app", { replace: true });
    } catch {
      setFormError("couldn't reach the server");
    }
  }

  async function handleSkip() {
    setFormError(undefined);
    try {
      await submit.mutateAsync({
        skipped: true,
        ...(displayName ? { display_name: displayName } : {}),
      });
      navigate("/app", { replace: true });
    } catch {
      setFormError("couldn't reach the server");
    }
  }

  if (shouldRedirect) return <Navigate to="/app" replace />;

  return (
    <main className="relative min-h-dvh overflow-hidden">
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-y-0 right-0 hidden w-2/3 [background-image:var(--graph-rule)] [mask-image:linear-gradient(to_right,transparent,black_60%)] md:block"
      />

      <div className="relative flex min-h-dvh items-center px-6 py-12 md:px-[max(6vw,48px)]">
        <div className="w-full max-w-[460px]">
          <Logo size={20} animated={false} glowStrength={0} />

          <h1 className="mt-8 text-h2 text-foreground">Tell us a bit about you</h1>
          <p className="mt-2 text-lead text-muted-foreground">
            So AyuMind can compute targets that are actually yours.
          </p>

          {profile.isPending ? (
            <div className="mt-8 flex flex-col gap-4">
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-10 w-2/3" />
            </div>
          ) : (
            <form onSubmit={handleSubmit} noValidate className="mt-8 flex flex-col gap-6">
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                <Field
                  label="Name"
                  name="display_name"
                  autoFocus
                  required
                  value={displayName}
                  onChange={(e) => setDisplayName(e.target.value)}
                />
                <Field
                  label="Date of birth"
                  type="date"
                  name="date_of_birth"
                  required
                  value={dateOfBirth}
                  onChange={(e) => setDateOfBirth(e.target.value)}
                />
              </div>

              <div className="flex flex-col gap-1.5">
                <span className="font-mono text-micro uppercase tracking-[0.08em] text-faint">
                  Sex
                </span>
                <div className="flex flex-wrap items-center gap-2">
                  <ToggleGroup<Sex>
                    options={SEX_OPTIONS}
                    value={sexSkipped ? null : sex}
                    onChange={chooseSex}
                    labelOf={sexLabel}
                  />
                  <Button
                    type="button"
                    variant={sexSkipped ? "primary" : "secondary"}
                    size="md"
                    aria-pressed={sexSkipped}
                    onClick={() => setSexSkipped((v) => !v)}
                  >
                    Prefer not to say
                  </Button>
                </div>
                <p className="text-meta text-faint">
                  Used to compute your target — skipping widens the estimate rather than guessing.
                </p>
              </div>

              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                <Field
                  label={`Height (${units === "metric" ? "cm" : "in"})`}
                  type="number"
                  inputMode="decimal"
                  name="height"
                  required
                  value={heightInput}
                  onChange={(e) => setHeightInput(e.target.value)}
                />
                <Field
                  label={`Current weight (${units === "metric" ? "kg" : "lb"})`}
                  type="number"
                  inputMode="decimal"
                  name="weight"
                  required
                  value={weightInput}
                  onChange={(e) => setWeightInput(e.target.value)}
                />
              </div>

              <div className="flex flex-col gap-1.5">
                <span className="font-mono text-micro uppercase tracking-[0.08em] text-faint">
                  Primary goal
                </span>
                <ToggleGroup<PrimaryGoal>
                  options={PRIMARY_GOALS}
                  value={primaryGoal}
                  onChange={setPrimaryGoal}
                  labelOf={goalLabel}
                />
              </div>

              <div className="flex flex-col gap-1.5">
                <span className="font-mono text-micro uppercase tracking-[0.08em] text-faint">
                  Activity level
                </span>
                <ToggleGroup<ActivityLevel>
                  options={ACTIVITY_LEVELS}
                  value={activityLevel}
                  onChange={setActivityLevel}
                  labelOf={activityLabel}
                />
              </div>

              {/* The payoff — appears the moment the required fields resolve to a computable
                  target (§6.19). Mono because it is an engine-shaped number, same convention as
                  every other database-originated value in this product. */}
              <div
                className={cn(
                  "rounded-sm border border-border bg-surface px-4 py-3 transition-opacity duration-180",
                  preview ? "opacity-100" : "pointer-events-none opacity-0",
                )}
                aria-live="polite"
              >
                {preview && (
                  <>
                    <p className="font-mono text-body text-signal">
                      ~{preview.proteinG}g protein · ~{preview.calorieKcal} kcal
                    </p>
                    <p className="mt-1 text-meta text-faint">
                      based on your profile — adjust anytime
                    </p>
                    <details className="mt-1">
                      <summary className="cursor-pointer text-meta text-muted-foreground">
                        how this was calculated
                      </summary>
                      <p className="mt-1 text-meta text-faint">{preview.basis}</p>
                    </details>
                  </>
                )}
              </div>

              <div>
                <button
                  type="button"
                  className="text-dense text-muted-foreground underline decoration-border underline-offset-4 transition-colors duration-120 hover:decoration-foreground"
                  onClick={() => setShowOptional((v) => !v)}
                  aria-expanded={showOptional}
                >
                  {showOptional ? "Hide" : "More about you (optional)"}
                </button>

                {showOptional && (
                  <div className="mt-4 flex flex-col gap-4">
                    <div className="flex flex-col gap-1.5">
                      <span className="font-mono text-micro uppercase tracking-[0.08em] text-faint">
                        Units
                      </span>
                      <ToggleGroup<"metric" | "imperial">
                        options={UNITS_OPTIONS}
                        value={units}
                        onChange={toggleUnits}
                        labelOf={unitsLabel}
                      />
                    </div>

                    <Field
                      label={`Target weight (${units === "metric" ? "kg" : "lb"})`}
                      type="number"
                      inputMode="decimal"
                      name="target_weight"
                      value={targetWeightInput}
                      onChange={(e) => setTargetWeightInput(e.target.value)}
                    />

                    <Field
                      label="Dietary preference"
                      name="dietary_preference"
                      placeholder="vegetarian, vegan, omnivore…"
                      value={dietaryPreference}
                      onChange={(e) => setDietaryPreference(e.target.value)}
                    />

                    <div className="flex flex-col gap-1.5">
                      <span className="font-mono text-micro uppercase tracking-[0.08em] text-faint">
                        Allergies / restrictions
                      </span>
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
                              onClick={() =>
                                setAllergies((prev) => prev.filter((a) => a !== item))
                              }
                              className="rounded-sm border border-border bg-surface-2 px-2 py-1 text-meta text-foreground transition-colors duration-120 hover:border-invalid hover:text-invalid"
                            >
                              {item} ×
                            </button>
                          ))}
                        </div>
                      )}
                    </div>
                  </div>
                )}
              </div>

              {formError && (
                <p role="alert" className="text-meta text-invalid">
                  {formError}
                </p>
              )}

              <div className="flex items-center gap-4">
                <Button type="submit" variant="primary" size="lg" isLoading={submit.isPending}>
                  Continue
                </Button>
                <button
                  type="button"
                  onClick={handleSkip}
                  disabled={submit.isPending}
                  className="text-dense text-muted-foreground underline decoration-border underline-offset-4 transition-colors duration-120 hover:decoration-foreground disabled:pointer-events-none disabled:opacity-50"
                >
                  Skip for now
                </button>
              </div>
            </form>
          )}
        </div>
      </div>
    </main>
  );
}
