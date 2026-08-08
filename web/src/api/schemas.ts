/**
 * Runtime contracts for the glass-box read API.
 *
 * The backend is Python (FastAPI); there are no shared types across the wire. These schemas are
 * the boundary where a contract change becomes a loud, located error instead of `undefined`
 * three components deep. They mirror `api/routers/glassbox.py` field for field.
 *
 * They also *are* the TypeScript types — every type below is inferred, never hand-written, so a
 * schema and its type cannot disagree.
 *
 * Parse at the fetch, never inside a component (see `client.ts`).
 */

import { z } from "zod";

/* ── primitives ──────────────────────────────────────────────────────────────────────────── */

/** Serialized by Python's `datetime.isoformat()`, which omits the trailing `Z`. */
const IsoDateTime = z.string().min(1);
const Uuid = z.string().uuid();

/* ── memory rows (POST /api/memories/batch) ──────────────────────────────────────────────── */

/**
 * A hydrated memory. Unlike `EvidenceSnapshot` inside the trace, this carries `payload` — a
 * snapshot travels *with* the trace where copies would duplicate truth, whereas this is the UI
 * resolving a chip against the live row, and the payload is what the user wants to see.
 */
export const MemoryRow = z.object({
  id: Uuid,
  event_time: IsoDateTime,
  tz: z.string(),
  type: z.string(),
  source: z.string(),
  provenance: z.string(),
  confidence: z.number(),
  status: z.string(),
  superseded_by: Uuid.nullable(),
  summary: z.string().nullable(),
  payload: z.record(z.string(), z.unknown()),
  created_at: IsoDateTime,
});

export const BatchMemoriesResponse = z.object({
  memories: z.array(MemoryRow),
  /**
   * IDs the caller cannot see. A trace may legitimately cite a row that was later superseded, so
   * these are *omitted rather than rejected* — one stale chip must not fail the whole evidence
   * pane. The UI renders an honest placeholder for each (DESIGN.md §6.11).
   */
  missing: z.array(Uuid),
});

/* ── the trace (GET /api/turns/{turn_id}/trace) ──────────────────────────────────────────── */

/** One executed query. This is what "how this was retrieved" renders. */
export const RetrievalStep = z.object({
  family: z.string(),
  sql: z.string(),
  params: z.record(z.string(), z.unknown()),
  row_count: z.number(),
});

/** Deliberately payload-free — see `MemoryRow` above. */
export const EvidenceSnapshot = z.object({
  id: Uuid,
  type: z.string(),
  event_time: IsoDateTime,
  confidence: z.number(),
  provenance: z.string(),
  summary: z.string().nullable(),
});

/**
 * A derived insight that participated in the answer.
 *
 * `evidence_ids` is its lineage and is **rendered, never cited** (Q1, resolved narrow): the
 * narrator saw the hypothesis, not the rows beneath it, so these IDs are deliberately absent
 * from `citable_ids`. Do not treat them as citable in the UI.
 *
 * `pattern_strength` and `retraction` are additive (M8) — `.default()`, not required, because
 * the trace is served verbatim (I-29) and a row persisted before M8 simply lacks these keys.
 * `retraction` is already-rendered prose (`engine.insights.render_retraction_condition`), never
 * a structured condition: rule 16 forbids deriving display copy from data client-side.
 */
export const InsightRef = z.object({
  id: Uuid,
  hypothesis: z.string(),
  evidence_ids: z.array(Uuid),
  pattern_strength: z.number().default(0),
  retraction: z.string().nullable().default(null),
});

/** Why a candidate was selected — the four committed scoring axes plus the composite. */
export const RankingEntry = z.object({
  memory_id: Uuid,
  relevance: z.number(),
  confidence: z.number(),
  recency: z.number(),
  tier: z.number(),
  score: z.number(),
});

/**
 * The ADR-12 contract, field for field.
 *
 * Served **verbatim** (I-29): never merged with live data, never re-derived. A rendered glass box
 * that can drift from what the turn actually did is worse than no glass box, because it looks
 * authoritative while being wrong.
 */
export const EvidenceTrace = z.object({
  trace_id: Uuid,
  question: z.string(),
  retrieval_steps: z.array(RetrievalStep),
  evidence: z.array(EvidenceSnapshot),
  insights: z.array(InsightRef),
  timeline: z.array(EvidenceSnapshot),
  ranking: z.array(RankingEntry),
  assembled_at: IsoDateTime,
  /**
   * Every memory ID the narrator was permitted to cite. **Not derivable from `evidence`** — an
   * aggregate's contributing IDs are citable but never appear as snapshots (ADR-14.7), so a UI
   * that built its citable set from `evidence` would flag correct citations as invalid.
   */
  citable_ids: z.array(Uuid),
});

/**
 * Recomputed on read from the persisted answer + `trace.citable_ids`. It is derived rather than
 * stored precisely because it is a pure function of two persisted values, so it cannot drift.
 *
 * `status` is honestly scoped (ADR-13.13): `valid` means every marker *resolves*, not that the
 * claim attached to it is true. UI copy must never say "verified".
 */
export const CitationReport = z.object({
  cited: z.array(Uuid),
  invalid: z.array(z.string()),
  status: z.enum(["valid", "invalid", "uncited"]),
  citable_count: z.number(),
});

export const TraceResponse = z.object({
  turn_id: Uuid,
  thread_id: z.string(),
  answer: z.string().nullable(),
  memory_ids: z.array(Uuid),
  trace: EvidenceTrace,
  citation_report: CitationReport,
  recorded_at: IsoDateTime,
});

/* ── history (GET /api/turns) ────────────────────────────────────────────────────────────── */

export const Turn = z.object({
  id: Uuid,
  role: z.enum(["user", "assistant"]),
  content: z.string().nullable(),
  thread_id: z.string(),
  memory_ids: z.array(Uuid),
  /** Whether a glass box exists to open. A turn without one is honest — stage (G) is best-effort. */
  has_trace: z.boolean(),
  created_at: IsoDateTime,
});

export const TurnsResponse = z.object({ turns: z.array(Turn) });

/* ── stats + timeline ────────────────────────────────────────────────────────────────────── */

/**
 * All zeros is a valid, honest response, not an error: every account starts empty (ADR-13.4),
 * including every judge's. This shape is what the "your memory starts here" empty states are
 * built on, and it is pinned by test in `api/tests/test_glassbox.py`.
 */
export const StatsResponse = z.object({
  memories: z.number(),
  insights: z.number(),
  days: z.number(),
  first_event: IsoDateTime.nullable(),
  last_event: IsoDateTime.nullable(),
});

export const TimelineDay = z.object({
  day: z.string(),
  n: z.number(),
  insights: z.number(),
});

export const TimelineResponse = z.object({ days: z.array(TimelineDay) });

/* ── auth (POST /api/auth/{signup,login,logout}) ─────────────────────────────────────────── */

export const AuthResponse = z.object({
  user_id: Uuid,
  email: z.string(),
});

/* ── a conversational turn (POST /api/chat) ──────────────────────────────────────────────── */

/** One memory a turn created. `embedding_pending` is honest: the row exists, the vector may not. */
export const MemoryRef = z.object({
  id: Uuid,
  type: z.string(),
  summary: z.string().nullable(),
  embedding_pending: z.boolean(),
});

/**
 * Proof that talking is logging (§6.7).
 *
 * `insights` is deliberately separate from `created`, not folded in: the user *reported* what is
 * in `created`, while an insight is a claim the engine made about it. Rendering them as one list
 * would imply the user logged something they never said.
 */
export const Receipt = z.object({
  parse_status: z.string(),
  message: z.string(),
  superseded_note_id: Uuid.nullable(),
  created: z.array(MemoryRef),
  insights: z.array(MemoryRef),
});

export const ChatResponse = z.object({
  thread_id: z.string(),
  answer: z.string(),
  citations: z.array(Uuid),
  /** Null when the turn assembled no context (an ingest-only turn has nothing to cite). */
  citation_report: CitationReport.nullable(),
  receipts: z.array(Receipt),
  trace: EvidenceTrace.nullable(),
  /** Stage (G)'s handle. Null means the turn was not recorded — the answer stands, the glass
   * box does not. The UI must treat this as "no glass box for this turn", never as an error. */
  turn_id: Uuid.nullable(),
  /** Retrieval the engine refused. Surfaced rather than swallowed: the answer may be partial. */
  errors: z.array(z.string()),
});

/* ── inferred types — never hand-write these ─────────────────────────────────────────────── */

export type AuthResponse = z.infer<typeof AuthResponse>;
export type MemoryRef = z.infer<typeof MemoryRef>;
export type Receipt = z.infer<typeof Receipt>;
export type ChatResponse = z.infer<typeof ChatResponse>;

export type MemoryRow = z.infer<typeof MemoryRow>;
export type BatchMemoriesResponse = z.infer<typeof BatchMemoriesResponse>;
export type RetrievalStep = z.infer<typeof RetrievalStep>;
export type EvidenceSnapshot = z.infer<typeof EvidenceSnapshot>;
export type InsightRef = z.infer<typeof InsightRef>;
export type RankingEntry = z.infer<typeof RankingEntry>;
export type EvidenceTrace = z.infer<typeof EvidenceTrace>;
export type CitationReport = z.infer<typeof CitationReport>;
export type TraceResponse = z.infer<typeof TraceResponse>;
export type Turn = z.infer<typeof Turn>;
export type TurnsResponse = z.infer<typeof TurnsResponse>;
export type StatsResponse = z.infer<typeof StatsResponse>;
export type TimelineDay = z.infer<typeof TimelineDay>;
export type TimelineResponse = z.infer<typeof TimelineResponse>;
