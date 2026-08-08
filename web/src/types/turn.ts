/**
 * The conversation's local turn model.
 *
 * Deliberately not the API shape. `GET /api/turns` returns persisted history without receipts,
 * while `POST /api/chat` returns a live turn *with* them, and the UI needs one list containing
 * both plus an in-flight placeholder. A discriminated union keeps every render path exhaustive —
 * TypeScript will not let a new turn kind be forgotten in the renderer.
 */

import type { CitationReport, EvidenceTrace, Receipt } from "@/api/schemas";

export type ChatTurn =
  | { kind: "user"; id: string; content: string }
  | {
      kind: "assistant";
      id: string;
      content: string;
      receipts: Receipt[];
      citations: string[];
      citationReport: CitationReport | null;
      /** The trace as it arrived inline with the live turn. Null for history-seeded turns, whose
       * trace is fetched on demand by `turnId` — that is what makes an old answer inspectable
       * instead of inert. */
      trace: EvidenceTrace | null;
      /** Stage (G)'s handle, or null when the turn was not recorded. Null means "no glass box
       * for this turn" — the answer stands; it is not an error state. */
      turnId: string | null;
      errors: string[];
    }
  | { kind: "pending"; id: string }
  /**
   * A turn the server could not complete.
   *
   * It stays in the conversation rather than vanishing, and it carries the original `message` so
   * one click resends it. Silently dropping a failed turn and pushing the text back into the
   * composer looked like the message bounced for no reason — the user is owed the reason and the
   * action (§6.11), and never-lose-input (ADR-13.5) holds either way.
   */
  | { kind: "failed"; id: string; message: string; detail: string };
