/**
 * Which stored fields are plumbing, and therefore not shown by default.
 *
 * The glass box's job is to prove a claim to the person reading it. A claim fingerprint, a
 * replay import id, or the account's own UUID prove nothing to them — they are internal
 * bookkeeping that the engine needs and a reader does not. Rendered beside real evidence they
 * do active harm: they pad the pane, they read as leaked internals, and they bury the two or
 * three lines that actually answer the question.
 *
 * **Hidden is not deleted.** Everything here is one click away in `Raw data`, and the plain
 * view states how many fields it set aside. That is the line this module walks: the default
 * view is *edited*, the technical view is *complete*, and the reader is told which one they are
 * looking at. A view that silently dropped fields would make the raw toggle the only honest
 * one, which would defeat having a default at all.
 *
 * Everything in the pane is already scoped to the signed-in account (`api/routers/glassbox.py`
 * enforces that per route, and `api/tests/test_scoping.py` proves it) — so this is about not
 * showing someone their own plumbing, not about cross-account exposure.
 */

/**
 * Payload keys the plain view sets aside.
 *
 * Deliberately short. The test for membership is "does this exist for the engine's benefit
 * rather than the reader's" — not "is it ugly". Values a user could act on stay visible even
 * when they are technical: `pattern_strength` and its three factors are published on purpose
 * (they are what stops a heuristic from reading as a fact), and blood markers are the whole
 * point of a blood report.
 */
export const INTERNAL_PAYLOAD_KEYS: ReadonlySet<string> = new Set([
  "fingerprint", // identity hash of a claim — meaningful only to the deduplicator
  "replay_record_id", // id from the history-import ledger
  "composition", // the payload table's key for a reconstructed pattern
  "macros_source", // which reviewed table supplied a replayed meal's macros
  "pipeline", // engine.nutrition/vN
  "prompt_version", // nutrition/2026-08-09
  "photo_s3_key", // internal object-storage path
  "content_hash",
  "schema_version",
]);

/**
 * Query parameters the plain view sets aside, and `user_id`, which is set aside everywhere.
 *
 * `user_id` is the one field here that is never shown in either view. Every row in the pane
 * belongs to the signed-in account by construction, so printing the account's UUID beside each
 * query tells the reader nothing they did not already know and hands them an internal
 * identifier for no reason. The SQL still shows the `%(user_id)s` placeholder, so the fact that
 * every query is scoped to one account — the security property worth seeing — remains visible.
 */
export const ALWAYS_HIDDEN_PARAMS: ReadonlySet<string> = new Set(["user_id"]);

/** Bound parameters that are engine internals: JSONB paths, vectors, row caps. */
export const INTERNAL_PARAMS: ReadonlySet<string> = new Set([
  "path",
  "est_path",
  "qvec",
  "limit",
  "period",
]);

export function isInternalPayloadKey(key: string): boolean {
  return INTERNAL_PAYLOAD_KEYS.has(key);
}
