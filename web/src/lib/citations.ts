/**
 * Splitting narration into prose and citation markers.
 *
 * **This is the one place the UI touches model output, and it extracts exactly one thing: where
 * the markers are.** ADR-12 forbids deriving structured data from narration — no dates, no
 * numbers, no summaries are read from this text. The marker yields a memory ID; everything a chip
 * then displays comes from the hydrated database row.
 *
 * The pattern mirrors `engine/citations.py::CITATION_RE` exactly. If one changes, both change:
 * a client that parses a wider set than the validator would render chips the engine never
 * blessed, which is precisely the drift the mechanical validator exists to prevent.
 */

/** Same shape as the server's validator: a bracketed UUID. */
const CITATION_RE =
  /\[([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\]/g;

export type AnswerSegment =
  | { kind: "text"; text: string }
  | { kind: "citation"; id: string };

/**
 * Split an answer into ordered segments.
 *
 * Returns text verbatim between markers — including whitespace and punctuation — so the
 * reassembled output is character-identical to what the model produced. Anything that looks like
 * a marker but is not a well-formed UUID stays plain text rather than becoming a broken chip.
 */
export function parseAnswer(answer: string): AnswerSegment[] {
  const segments: AnswerSegment[] = [];
  let lastIndex = 0;

  // `matchAll` on a fresh iterator avoids the shared-lastIndex footgun that a module-level
  // /g regex has when reused across calls.
  for (const match of answer.matchAll(CITATION_RE)) {
    const start = match.index;
    if (start > lastIndex) {
      segments.push({ kind: "text", text: answer.slice(lastIndex, start) });
    }
    segments.push({ kind: "citation", id: (match[1] as string).toLowerCase() });
    lastIndex = start + match[0].length;
  }

  if (lastIndex < answer.length) {
    segments.push({ kind: "text", text: answer.slice(lastIndex) });
  }
  return segments;
}

/** Every distinct ID cited in an answer, in first-appearance order. */
export function citedIds(answer: string): string[] {
  const seen = new Set<string>();
  for (const segment of parseAnswer(answer)) {
    if (segment.kind === "citation") seen.add(segment.id);
  }
  return [...seen];
}
