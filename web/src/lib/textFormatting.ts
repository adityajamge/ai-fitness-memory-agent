/**
 * Minimal inline formatting for narration — bold only.
 *
 * A separate, narrower touch-point than `lib/citations.ts` on purpose: that module extracts a
 * memory ID, which is structured data and stays under ADR-12's "never derive structured data
 * from narration" discipline. This extracts nothing — `**text**` to `<strong>text</strong>` is
 * typographic, not a claim, so it doesn't need the same scrutiny. Deliberately not a markdown
 * library: the model only ever needs emphasis, and italics/headers/lists/links would be scope
 * this chat has no use for.
 */

export interface FormattedRun {
  bold: boolean;
  text: string;
}

const BOLD_RE = /\*\*(.+?)\*\*/g;

/** Splits text on `**bold**` runs. Unpaired or malformed `**` is left as literal text — same
 * "don't get clever" posture `parseAnswer` follows for a malformed citation marker. */
export function parseBold(text: string): FormattedRun[] {
  const runs: FormattedRun[] = [];
  let lastIndex = 0;

  for (const match of text.matchAll(BOLD_RE)) {
    const start = match.index;
    if (start > lastIndex) runs.push({ bold: false, text: text.slice(lastIndex, start) });
    runs.push({ bold: true, text: match[1] as string });
    lastIndex = start + match[0].length;
  }

  if (lastIndex < text.length) runs.push({ bold: false, text: text.slice(lastIndex) });
  return runs;
}
