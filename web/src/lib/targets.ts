/**
 * Client-side mirror of `engine/profile.py`'s `compute_targets` (Mifflin-St Jeor BMR x
 * activity x goal — ADR-17.4, DESIGN.md §6.19).
 *
 * Used ONLY for the onboarding/profile screen's live preview line, which has to update on
 * every keystroke without a round trip. The server recomputes and stores the authoritative
 * value on submit (`api/routers/profile.py`), so a drift here is a UX cosmetic issue, never a
 * stored-data one — but keep this in sync with `engine/profile.py` by hand; there is no
 * shared-language boundary to enforce it automatically.
 */

export type Sex = "male" | "female";
export type ActivityLevel = "sedentary" | "light" | "moderate" | "very_active" | "athlete";
export type PrimaryGoal = "lose_fat" | "build_muscle" | "recomp" | "maintain" | "general_health";

export const ACTIVITY_LEVELS: readonly ActivityLevel[] = [
  "sedentary",
  "light",
  "moderate",
  "very_active",
  "athlete",
];

export const PRIMARY_GOALS: readonly PrimaryGoal[] = [
  "lose_fat",
  "build_muscle",
  "recomp",
  "maintain",
  "general_health",
];

// Shared by Onboarding and ProfileSettings — hoisted here, not declared inline at either call
// site (fixed 2026-08-13). `ToggleGroup` is `React.memo`'d, and memoization only pays off when
// its props are referentially stable: an inline `options={["male", "female"]}` array literal or
// an inline `labelOf={(v) => ...}` arrow function is a *new* value on every render, which
// re-renders the group (and its up-to-five `Button` children) on every keystroke in the form
// regardless of the memo — a large form with several groups made typing visibly heavy.
export const SEX_OPTIONS: readonly Sex[] = ["male", "female"];
export const UNITS_OPTIONS: readonly ("metric" | "imperial")[] = ["metric", "imperial"];

export function sexLabel(v: Sex): string {
  return v === "male" ? "Male" : "Female";
}

export function unitsLabel(v: "metric" | "imperial"): string {
  return v === "metric" ? "kg / cm" : "lb / in";
}

export const GOAL_LABEL: Record<PrimaryGoal, string> = {
  lose_fat: "Lose fat",
  build_muscle: "Build muscle",
  recomp: "Recomp",
  maintain: "Maintain",
  general_health: "General health",
};

export function goalLabel(v: PrimaryGoal): string {
  return GOAL_LABEL[v];
}

export const ACTIVITY_LABEL: Record<ActivityLevel, string> = {
  sedentary: "Sedentary",
  light: "Lightly active",
  moderate: "Moderately active",
  very_active: "Very active",
  athlete: "Athlete",
};

export function activityLabel(v: ActivityLevel): string {
  return ACTIVITY_LABEL[v];
}

const ACTIVITY_MULTIPLIER: Record<ActivityLevel, number> = {
  sedentary: 1.2,
  light: 1.375,
  moderate: 1.55,
  very_active: 1.725,
  athlete: 1.9,
};

const GOAL_CALORIE_ADJUST: Record<PrimaryGoal, number> = {
  lose_fat: -500,
  build_muscle: 300,
  recomp: 0,
  maintain: 0,
  general_health: 0,
};

const GOAL_PROTEIN_PER_KG: Record<PrimaryGoal, number> = {
  lose_fat: 2.0,
  build_muscle: 1.8,
  recomp: 2.0,
  maintain: 1.6,
  general_health: 1.6,
};

const MIN_CALORIE_FLOOR = 1200;

export interface TargetPreview {
  proteinG: number;
  calorieKcal: number;
  basis: string;
}

function ageYears(dob: Date, today: Date): number {
  let age = today.getFullYear() - dob.getFullYear();
  const beforeBirthday =
    today.getMonth() < dob.getMonth() ||
    (today.getMonth() === dob.getMonth() && today.getDate() < dob.getDate());
  if (beforeBirthday) age -= 1;
  return age;
}

/** Mirrors `engine.profile.compute_targets`: returns `null` rather than guessing when the
 * inputs cannot support a computation (ADR-17.4's "decline, never invent" posture). */
export function previewTargets(input: {
  weightKg: number | null;
  heightCm: number | null;
  dateOfBirth: string | null; // YYYY-MM-DD
  sex: Sex | null;
  activityLevel: ActivityLevel | null;
  primaryGoal: PrimaryGoal | null;
}): TargetPreview | null {
  const { weightKg, heightCm, dateOfBirth, sex, activityLevel, primaryGoal } = input;
  if (
    !weightKg ||
    weightKg <= 0 ||
    !heightCm ||
    heightCm <= 0 ||
    !dateOfBirth ||
    !activityLevel
  ) {
    return null;
  }
  const dob = new Date(dateOfBirth);
  if (Number.isNaN(dob.getTime())) return null;
  const age = ageYears(dob, new Date());
  if (age <= 0) return null;

  const goal: PrimaryGoal = primaryGoal && primaryGoal in GOAL_CALORIE_ADJUST ? primaryGoal : "maintain";

  let bmr: number;
  let sexBasis: string;
  if (sex === "male") {
    bmr = 10 * weightKg + 6.25 * heightCm - 5 * age + 5;
    sexBasis = "male";
  } else if (sex === "female") {
    bmr = 10 * weightKg + 6.25 * heightCm - 5 * age - 161;
    sexBasis = "female";
  } else {
    // Midpoint of the male (+5) / female (-161) offsets — averaged, never guessed.
    bmr = 10 * weightKg + 6.25 * heightCm - 5 * age - 78;
    sexBasis = "unspecified (sex-averaged)";
  }

  const multiplier = ACTIVITY_MULTIPLIER[activityLevel];
  const tdee = bmr * multiplier;
  const adjust = GOAL_CALORIE_ADJUST[goal];
  const calorieKcal = Math.max(MIN_CALORIE_FLOOR, Math.round(tdee + adjust));
  const proteinPerKg = GOAL_PROTEIN_PER_KG[goal];
  const proteinG = Math.round(proteinPerKg * weightKg * 10) / 10;

  const basis =
    `Mifflin-St Jeor BMR (${sexBasis}, age ${age}, ${weightKg}kg, ${heightCm}cm) ` +
    `x ${activityLevel.replace("_", " ")} activity (${multiplier}x) = ${Math.round(tdee)} kcal TDEE; ` +
    `${goal.replace("_", " ")} adjustment ${adjust >= 0 ? "+" : ""}${adjust} kcal; ` +
    `protein at ${proteinPerKg} g/kg bodyweight`;

  return { proteinG, calorieKcal, basis };
}

export const LB_PER_KG = 2.20462;
export const IN_PER_CM = 0.393701;

export const kgToLb = (kg: number): number => kg * LB_PER_KG;
export const lbToKg = (lb: number): number => lb / LB_PER_KG;
export const cmToIn = (cm: number): number => cm * IN_PER_CM;
export const inToCm = (inches: number): number => inches / IN_PER_CM;
