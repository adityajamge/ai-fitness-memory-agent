/**
 * Narration with its citations resolved — DESIGN.md §6.6.
 *
 * Renders the model's prose verbatim and swaps each `[uuid]` marker for a chip. Text between
 * markers is untouched, so what the user reads is exactly what the model wrote; only the markers
 * become interactive.
 *
 * The three chip states come from three different sources, and keeping them straight is the whole
 * point of the glass box:
 *
 * - **invalid** — the engine's `citation_report.invalid` says this marker resolves to nothing.
 *   The narrator cited something it was not given.
 * - **unresolvable** — the ID was citable, but `POST /api/memories/batch` reported it in
 *   `missing`: superseded or removed since the turn ran. Not the narrator's fault.
 * - **resolved** — hydrated, clickable, and it walks the user to the row.
 */

import { Fragment } from "react";
import type { CitationReport, MemoryRow } from "@/api/schemas";
import { parseAnswer } from "@/lib/citations";
import { parseBold } from "@/lib/textFormatting";
import { CitationChip, type ChipState } from "./CitationChip";

export interface AnswerProps {
  text: string;
  /** Hydrated rows for the turn's citable set, keyed by id. */
  rows: Map<string, MemoryRow>;
  /** IDs the batch endpoint could not return — superseded or removed. */
  missing: Set<string>;
  report: CitationReport | null;
  activeId: string | null;
  onActivate: (id: string) => void;
}

export function Answer({ text, rows, missing, report, activeId, onActivate }: AnswerProps) {
  const invalid = new Set((report?.invalid ?? []).map((s) => s.toLowerCase()));

  return (
    // 72ch (§5.7 / F-T3): the column may grow with the viewport, the measure may not.
    <p className="max-w-[72ch] text-body whitespace-pre-wrap text-foreground">
      {parseAnswer(text).map((segment, i) => {
        if (segment.kind === "text") {
          return (
            <span key={i}>
              {parseBold(segment.text).map((run, j) =>
                run.bold ? (
                  <strong key={j} className="font-medium">
                    {run.text}
                  </strong>
                ) : (
                  // Fragment, not a span: `whitespace-pre-wrap` on the parent already preserves
                  // this text's line breaks, and an extra inline element per plain-text run would
                  // be free structure with no purpose.
                  <Fragment key={j}>{run.text}</Fragment>
                ),
              )}
            </span>
          );
        }

        const row = rows.get(segment.id);
        const state: ChipState = invalid.has(segment.id)
          ? "invalid"
          : row
            ? "resolved"
            : missing.has(segment.id)
              ? "unresolvable"
              : // Not in the hydration set at all. Either the batch is still in flight, or this
                // is a history turn whose own trace has not been fetched (M6). Saying
                // "superseded or removed" here would assert a deletion we have not verified.
                "unloaded";

        return (
          <CitationChip
            key={i}
            id={segment.id}
            row={row}
            state={state}
            isActive={activeId === segment.id}
            onActivate={onActivate}
          />
        );
      })}
    </p>
  );
}
