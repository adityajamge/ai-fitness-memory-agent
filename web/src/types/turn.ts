/**
 * The conversation's local turn model.
 *
 * Deliberately not the API shape. `GET /api/turns` returns persisted history without receipts,
 * while `POST /api/chat` returns a live turn *with* them, and the UI needs one list containing
 * both plus an in-flight placeholder. A discriminated union keeps every render path exhaustive —
 * TypeScript will not let a new turn kind be forgotten in the renderer.
 */

import type { CitationReport, Receipt } from "@/api/schemas";

export type ChatTurn =
  | { kind: "user"; id: string; content: string }
  | {
      kind: "assistant";
      id: string;
      content: string;
      receipts: Receipt[];
      citations: string[];
      citationReport: CitationReport | null;
      /** Stage (G)'s handle, or null when the turn was not recorded. Null means "no glass box
       * for this turn" — the answer stands; it is not an error state. */
      turnId: string | null;
      errors: string[];
    }
  | { kind: "pending"; id: string };
