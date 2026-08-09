/**
 * Turning a stored JSONB payload into words a person can read.
 *
 * The evidence pane's default view. The raw payload is the *proof*, but it is not an
 * explanation: `{"qty_g": 250}` is only obvious to someone who already knows the schema, and
 * the glass box exists to convince people who do not.
 *
 * **This module renames and formats. It never reorders by importance, and it never drops a
 * field.** That distinction is the whole design:
 *
 * - A plain view that *hid* fields would make the raw toggle a place where secrets live, and
 *   quietly undo the point of showing the database at all. Every key in the payload appears in
 *   both views; only the presentation differs.
 * - The one concession is **identifier lists**: sixteen UUIDs rendered in full are not more
 *   readable than "16 memory IDs", they are less. Those collapse to a count that names where
 *   the values are (`Raw data`). The field is still present and still labelled — what changes
 *   is that the plain view stops pretending a UUID is prose.
 * - Unknown keys are humanized generically rather than dropped, so a payload that grows a new
 *   attribute (the `extra="allow"` design in `engine/types.py` guarantees it will) degrades to
 *   a slightly-clumsy label instead of an invisible field.
 * - Nothing here reads model output — it reads the engine's stored payload, so DESIGN.md
 *   rule 16 is satisfied by construction.
 *
 * Units live in the field *names* (`qty_g`, `duration_min`, `body_fat_pct`), which is the
 * engine's convention. Splitting the unit off the name and rendering it beside the number is
 * what turns `duration_min: 45` into `Duration · 45 min`.
 */

/** Unit suffixes the engine bakes into field names, longest-first so `_ng_ml` wins over `_ml`. */
const UNIT_SUFFIXES: ReadonlyArray<readonly [suffix: string, unit: string]> = [
  ["_ng_ml", "ng/mL"],
  ["_pg_ml", "pg/mL"],
  ["_mg_dl", "mg/dL"],
  ["_ug_dl", "µg/dL"],
  ["_g_dl", "g/dL"],
  ["_u_l", "U/L"],
  ["_kcal", "kcal"],
  ["_pct", "%"],
  ["_min", "min"],
  ["_km", "km"],
  ["_kg", "kg"],
  ["_mg", "mg"],
  ["_ml", "mL"],
  ["_cm", "cm"],
  ["_g", "g"],
];

/**
 * Human labels for the fields we know about.
 *
 * Hand-written rather than derived, because the good label is rarely the field name with
 * underscores removed: `qty_g` is "Amount", not "Qty G", and `hba1c_pct` is "HbA1c", not
 * "Hba 1 C". Anything absent falls through to the generic humanizer below.
 */
const LABELS: Readonly<Record<string, string>> = {
  // meal
  meal_type: "Meal",
  items: "Foods",
  qty_g: "Amount",
  qty: "How many",
  qty_text: "You said",
  name: "Food",
  photo_s3_key: "Photo",
  // workout
  activity: "Activity",
  duration_min: "Duration",
  distance_km: "Distance",
  exercises: "Exercises",
  // sleep
  hours: "Hours slept",
  quality: "Quality",
  // body / weight
  body_fat_pct: "Body fat",
  weight_kg: "Weight",
  method: "Measured by",
  height_cm: "Height",
  bmi: "BMI",
  skeletal_muscle_kg: "Skeletal muscle",
  muscle_mass_kg: "Muscle mass",
  visceral_fat_grade: "Visceral fat grade",
  bmr_kcal: "Resting burn",
  // blood report
  panel: "Panel",
  markers: "Results",
  flagged: "Flagged",
  within_range: "Within range",
  interpretation: "Interpretation",
  vitamin_d_ng_ml: "Vitamin D",
  vitamin_b12_pg_ml: "Vitamin B12",
  ferritin_ng_ml: "Ferritin",
  ldl_mg_dl: "LDL cholesterol",
  hba1c_pct: "HbA1c",
  // supplement
  dose_mg: "Dose",
  category: "Category",
  // note
  text: "What you wrote",
  // insight (tier-2 derived memory)
  kind: "Pattern type",
  hypothesis: "What the engine noticed",
  series_metric: "About",
  series_kind: "Series type",
  window_start: "Evidence from",
  window_end: "Evidence to",
  pre_value: "Before",
  post_value: "After",
  evidence_count: "Supporting memories",
  evidence_ids: "Supporting memory IDs",
  pattern_strength: "Pattern strength",
  effect: "Effect",
  coverage: "Coverage",
  specificity: "Specificity",
  fingerprint: "Claim fingerprint",
  retraction_condition: "Would be withdrawn if",
  intervention_ids: "Interventions",
  intervention_outcome: "Intervention outcome",
  boundary: "Change dated to",
  direction: "Direction",
  window_days: "Looking back",
  min_count: "Needs at least",
  threshold: "Threshold",
  // replay provenance
  expanded_from: "Part of a recorded pattern",
  assertion: "Original note",
  cadence: "Repeats",
  composition: "Pattern key",
  period_start: "Pattern from",
  period_end: "Pattern to",
  evidence_id: "Source document",
  replay_record_id: "Import ID",
};

/** Keys the plain view skips because a dedicated component already renders them properly. */
export const HANDLED_ELSEWHERE: ReadonlySet<string> = new Set(["nutrition"]);

export interface Labelled {
  label: string;
  /** Unit symbol split off the field name, when the name carried one. */
  unit: string | null;
}

/**
 * A field name, as a person would say it.
 *
 * Known fields use the table above. Everything else is humanized: strip a trailing unit,
 * replace underscores, sentence-case. `omega_3_ratio` → `Omega 3 ratio`, not a dropped field.
 */
export function labelFor(key: string): Labelled {
  const known = LABELS[key];
  if (known) return { label: known, unit: unitOf(key) };

  const unit = unitOf(key);
  const bare = unit ? stripUnit(key) : key;
  const words = bare.replace(/_/g, " ").trim();
  const label = words ? words.charAt(0).toUpperCase() + words.slice(1) : key;
  return { label, unit };
}

function unitOf(key: string): string | null {
  for (const [suffix, unit] of UNIT_SUFFIXES) {
    if (key.endsWith(suffix) && key.length > suffix.length) return unit;
  }
  return null;
}

function stripUnit(key: string): string {
  for (const [suffix] of UNIT_SUFFIXES) {
    if (key.endsWith(suffix) && key.length > suffix.length) return key.slice(0, -suffix.length);
  }
  return key;
}

/**
 * A scalar, as a person would read it.
 *
 * Booleans become yes/no because `true` is jargon in a sentence about food. ISO timestamps
 * become a readable date — the *same instant*, reformatted, never rounded or relabelled.
 * Numbers are returned unchanged: rule 5 wants them tabular, and rounding a stored value here
 * would make the plain view disagree with the raw one, which is the one thing it must never do.
 */
export function formatScalar(value: unknown, key?: string): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "boolean") return value ? "yes" : "no";
  if (typeof value === "number") return String(value);
  if (typeof value === "string") {
    const asDate = readIsoDate(value);
    if (asDate) return asDate;
    if (key && ENUM_FIELDS.has(key)) return unsnake(value);
    return value;
  }
  return String(value);
}

/**
 * Fields whose values are a closed vocabulary the engine chose, so `intervention_outcome` can
 * safely become `Intervention outcome`.
 *
 * An allowlist rather than a rule about snake_case, because the rule misfires on data: a
 * `series_metric` of `protein_g` would become "Protein g", which is worse than leaving it
 * alone. Enum labels are ours to phrase; measured values are not.
 */
const ENUM_FIELDS: ReadonlySet<string> = new Set([
  "kind",
  "series_kind",
  "direction",
  "basis",
  "confidence_class",
  "qty_basis",
  "nutrition_basis",
  "status",
  "meal_type",
  "quality",
  "category",
  "cadence",
  "reason",
]);

function unsnake(value: string): string {
  if (!/^[a-z][a-z0-9_]*$/.test(value)) return value;
  const words = value.replace(/_/g, " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/** ISO-8601 → a short readable date, or null when the string is not one. */
function readIsoDate(value: string): string | null {
  if (!/^\d{4}-\d{2}-\d{2}([T ]|$)/.test(value)) return null;
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return null;
  const hasTime = value.includes("T") && !/T00:00(:00)?/.test(value);
  return parsed.toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    ...(hasTime ? { hour: "numeric", minute: "2-digit" } : {}),
  });
}

/**
 * A meal item as one line: `chicken breast · 250 g`, or `rice · you said "some"`.
 *
 * The `qty_text` branch is the honest one. When the user never gave a number, the plain view
 * must show their own words rather than the portion the estimator later assumed — that
 * assumption belongs to the nutrition derivation, which labels it as assumed. Showing it here
 * as if it were stated is precisely the conflation the whole feature exists to prevent.
 */
export function describeItem(item: Record<string, unknown>): string {
  const name = typeof item["name"] === "string" ? item["name"] : "unnamed";
  const parts: string[] = [];
  if (typeof item["qty_g"] === "number") parts.push(`${item["qty_g"]} g`);
  if (typeof item["qty"] === "number") parts.push(`× ${item["qty"]}`);
  if (typeof item["qty_text"] === "string") parts.push(`you said “${item["qty_text"]}”`);
  return parts.length > 0 ? `${name} · ${parts.join(" · ")}` : name;
}

export const isPlainObject = (v: unknown): v is Record<string, unknown> =>
  typeof v === "object" && v !== null && !Array.isArray(v);

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
/** A long unbroken hex run — a fingerprint, content hash, or S3 key. */
const OPAQUE_RE = /^[0-9a-f]{24,}$/i;

/**
 * Whether a value is a machine identifier rather than something a person reads.
 *
 * Used to decide presentation, never to decide inclusion. An identifier still appears in the
 * plain view — as a count, or on its own wrapping line — because the point is to stop rendering
 * it *as if* it were readable, not to hide that the field exists.
 */
export function isIdentifier(value: unknown): boolean {
  return typeof value === "string" && (UUID_RE.test(value) || OPAQUE_RE.test(value));
}

/** An array that is entirely identifiers — `evidence_ids` and friends. */
export function isIdentifierList(value: unknown): value is string[] {
  return Array.isArray(value) && value.length > 0 && value.every(isIdentifier);
}

/**
 * Text long enough that squeezing it into a right-hand value column would clip it.
 *
 * Above this it renders as a wrapping block under its label instead. The threshold is about
 * presentation only — no value is ever truncated, because a glass box that elides the evidence
 * is not showing the evidence.
 */
export const LONG_TEXT = 48;
