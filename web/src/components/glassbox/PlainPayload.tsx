/**
 * The stored payload, in plain English — DESIGN.md §6.4, §10.
 *
 * The default reading of an evidence row. `{"qty_g": 250}` proves something to someone who
 * already knows the schema; this proves it to everyone else, which is the entire point of a
 * glass box aimed at people who did not build it.
 *
 * **Same data, different presentation — never a subset.** Every key in the payload is rendered
 * (except `nutrition`, which has its own richer component directly above). A plain view that
 * omitted fields would turn the raw toggle into the place where the real data lives, and quietly
 * defeat showing the database at all.
 *
 * Two typography rules are load-bearing here and worth stating, because they are what keep this
 * from reading as generated prose:
 *
 * - **Labels are sans, values are mono** (rule 2 — mono means the database said it). The label
 *   is our word for the field; the value is the row. The eye can tell which is which.
 * - **Numbers are `tabular-nums`** (rule 5), so a column of amounts aligns.
 *
 * Nothing here reads model output (rule 16), and nothing is rounded or re-derived — a value that
 * disagreed with the raw view would make both untrustworthy.
 */

import {
  HANDLED_ELSEWHERE,
  LONG_TEXT,
  describeItem,
  formatScalar,
  isIdentifier,
  isIdentifierList,
  isPlainObject,
  labelFor,
} from "@/lib/payloadLabels";
import { isInternalPayloadKey } from "@/lib/internalFields";
import { cn } from "@/lib/utils";

/**
 * One `label · value` line.
 *
 * Two layouts, chosen by the value rather than by the caller. A short scalar sits in a right-hand
 * column, where a stack of them aligns into a readable table. Anything long — a hypothesis
 * sentence, a fingerprint — moves to its own wrapping block underneath, because forcing it into
 * that column is what clips a claim at the pane's edge. `min-w-0` on the label and
 * `wrap-break-word` on the value are what keep a long unbroken hex string from widening the pane
 * into a horizontal scroll.
 */
function Row({
  label,
  value,
  unit,
  indent = false,
}: {
  label: string;
  value: string;
  unit?: string | null;
  indent?: boolean;
}) {
  // A lone UUID is only 36 characters — under the length threshold, but still the wrong thing
  // to right-align in a narrow column, where it overflows rather than wraps.
  const isBlock = value.length > LONG_TEXT || isIdentifier(value);

  if (isBlock) {
    return (
      <div className={cn("border-t border-border py-1.5 first:border-t-0", indent && "pl-3")}>
        <span className="text-meta text-muted-foreground">{label}</span>
        <p className="mt-0.5 font-mono text-meta leading-relaxed wrap-break-word text-foreground">
          {value}
          {unit && <span className="ml-1 text-faint">{unit}</span>}
        </p>
      </div>
    );
  }

  return (
    <div
      className={cn(
        "flex items-baseline justify-between gap-3 border-t border-border py-1.5 first:border-t-0",
        indent && "pl-3",
      )}
    >
      <span className="min-w-0 text-meta text-muted-foreground">{label}</span>
      <span className="shrink-0 text-right font-mono text-meta tabular-nums text-foreground">
        {value}
        {unit && <span className="ml-1 text-faint">{unit}</span>}
      </span>
    </div>
  );
}

/** A label with its own nested block underneath (arrays, objects). */
function Group({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="border-t border-border py-1.5 first:border-t-0">
      <span className="text-meta text-muted-foreground">{label}</span>
      <div className="mt-1">{children}</div>
    </div>
  );
}

/** A bare value inside a group — an array entry with no label of its own. */
function Line({ value }: { value: string }) {
  return (
    <div className="pl-3 font-mono text-meta tabular-nums wrap-break-word text-foreground">
      {value}
    </div>
  );
}

/**
 * Render one payload entry, dispatching on the shape of its value.
 *
 * Recursion is capped at one nested level of objects: past that the structure *is* the
 * information, and the raw view renders it better than any flattening would. A payload deep
 * enough to hit the cap shows its remaining shape as compact JSON rather than pretending to
 * have explained it.
 */
function Entry({ name, value, depth }: { name: string; value: unknown; depth: number }) {
  const { label, unit } = labelFor(name);

  if (Array.isArray(value)) {
    if (value.length === 0) return <Row label={label} value="none" />;

    // Sixteen UUIDs in full are not more readable than "16 memory IDs" — they are less, and
    // they push everything worth reading off the pane. The field stays labelled and counted,
    // and the copy names where the values themselves are, so nothing is quietly withheld.
    if (isIdentifierList(value)) {
      return (
        <Row
          label={label}
          value={`${value.length} ID${value.length === 1 ? "" : "s"} · see Raw data`}
        />
      );
    }

    // Meal items get a purpose-built line: name, amount, and — when the user never gave a
    // number — their own words, rather than the portion the estimator later assumed.
    if (name === "items") {
      return (
        <Group label={label}>
          {value.map((item, i) => (
            <Line key={i} value={isPlainObject(item) ? describeItem(item) : formatScalar(item)} />
          ))}
        </Group>
      );
    }

    return (
      <Group label={label}>
        {value.map((item, i) =>
          isPlainObject(item) ? (
            <div key={i} className="pl-3">
              {Object.entries(item).map(([k, v]) => (
                <Entry key={k} name={k} value={v} depth={depth + 1} />
              ))}
            </div>
          ) : (
            <Line key={i} value={formatScalar(item)} />
          ),
        )}
      </Group>
    );
  }

  if (isPlainObject(value)) {
    const entries = Object.entries(value);
    if (entries.length === 0) return <Row label={label} value="none" />;
    if (depth >= 1) {
      return <Row label={label} value={JSON.stringify(value)} />;
    }
    return (
      <Group label={label}>
        {entries.map(([k, v]) => (
          <Entry key={k} name={k} value={v} depth={depth + 1} />
        ))}
      </Group>
    );
  }

  return <Row label={label} value={formatScalar(value, name)} unit={unit} indent={depth > 0} />;
}

export interface PlainPayloadProps {
  payload: Record<string, unknown>;
}

export function PlainPayload({ payload }: PlainPayloadProps) {
  const visible = Object.entries(payload).filter(
    ([key]) => !HANDLED_ELSEWHERE.has(key) && !isInternalPayloadKey(key),
  );
  const hiddenCount = Object.keys(payload).filter(isInternalPayloadKey).length;

  // A real state, not a defect: a memory whose only content is its summary (and, for meals, the
  // nutrition block above) has nothing left for this view to list.
  if (visible.length === 0 && hiddenCount === 0) {
    return (
      <p className="py-1.5 text-meta text-faint">
        Nothing else stored for this memory beyond what is shown above.
      </p>
    );
  }

  return (
    <div>
      {visible.map(([key, value]) => (
        <Entry key={key} name={key} value={value} depth={0} />
      ))}
      {/* Stated, not silent. The reader should know the view is edited and where the rest is —
          otherwise the raw toggle becomes the only trustworthy one. */}
      {hiddenCount > 0 && (
        <p className="border-t border-border pt-1.5 text-meta text-faint">
          {hiddenCount} technical {hiddenCount === 1 ? "field" : "fields"} hidden · see Raw data
        </p>
      )}
    </div>
  );
}
