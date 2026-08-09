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
  | {
      kind: "user";
      id: string;
      content: string;
      /** ISO timestamp — persisted `created_at` for history, wall-clock at send time for a live
       * turn. Navigation sugar only (timeline "click a day to scrub", §9): never rendered as
       * evidence, so the live-turn approximation does not touch the data-source rule (ADR-12). */
      createdAt?: string;
    }
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
      createdAt?: string;
    }
  | {
      kind: "pending";
      id: string;
      /** Every stage label seen so far, in arrival order, from real `event: stage` frames (M6).
       * Empty until the first one arrives, or for the whole turn on the plain-transport
       * fallback, which carries no stage information at all — §6.10 requires this be driven by
       * real events, never a timer standing in for progress. Kept as a growing trail (DESIGN.md
       * §6.10 amendment 2026-08-09) rather than overwritten, so the conversation shows the path
       * the engine took, not just where it currently is. */
      stages: string[];
    }
  /**
   * A turn the server could not complete.
   *
   * It stays in the conversation rather than vanishing, and it carries the original `message` so
   * one click resends it. Silently dropping a failed turn and pushing the text back into the
   * composer looked like the message bounced for no reason — the user is owed the reason and the
   * action (§6.11), and never-lose-input (ADR-13.5) holds either way.
   */
  | { kind: "failed"; id: string; message: string; detail: string };
