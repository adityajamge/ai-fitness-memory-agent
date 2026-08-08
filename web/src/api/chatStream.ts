/**
 * The SSE transport for one turn — DESIGN.md §6.10, §9.1, §11 "open risk".
 *
 * `POST /api/chat/stream` narrates a turn's stages as they happen instead of returning
 * everything at once. **Whether that works through the deployed ALB is unproven** (§11), so
 * this module treats the failure mode as a runtime signal rather than a fixed choice: a
 * connection that fails **before any progress was observed** throws `StreamUnavailableError`,
 * and the caller (`AppScreen`) silently retries through the plain, already-tested
 * `POST /api/chat` for that turn.
 *
 * **The one caveat that matters here.** A connection that fails *after* at least one `stage`
 * frame arrived throws `StreamInterruptedError` instead, and that one must NOT be silently
 * retried: the graph was observably running, which for an ingest turn can mean a memory already
 * committed server-side before the connection broke. Silently resending the same message would
 * risk a silent duplicate — the never-lose-input guarantee (ADR-13.5) cuts both ways, and
 * *duplicating* input is not more honest than losing it. `AppScreen` surfaces this the same way
 * it surfaces any other genuine turn failure: a visible "that message didn't go through, resend?"
 * card, which makes the retry a user decision instead of an invisible one.
 *
 * A turn the graph itself failed on (an `error` frame) is a third, different thing: a real turn
 * failure, reported as one, exactly like the plain endpoint's non-2xx would be.
 *
 * `EventSource` cannot send a POST body, so this hand-rolls the minimal SSE-over-`fetch` framing
 * the backend emits (`api/routers/chat.py`'s `_sse`): `event: <name>\ndata: <json>\n\n`.
 */

import { ApiError, ContractError } from "./client";
import { ChatResponse } from "./schemas";

/** Thrown when nothing has happened yet — safe for the caller to silently retry through the
 * plain transport, because the graph has not observably started (see the module doc's "one
 * caveat" for why "not observably" is doing real work in that sentence). */
export class StreamUnavailableError extends Error {
  constructor(reason: string) {
    super(`chat stream unavailable: ${reason}`);
    this.name = "StreamUnavailableError";
  }
}

/** Thrown when the stream broke down **after** the graph was already observed to be running (at
 * least one `stage` frame arrived) but before a `done`/`error` frame closed it out. This must
 * NOT trigger a silent retry: an `ingest` stage already having been seen means the turn may have
 * already committed a memory server-side, and auto-resending the same message would risk a
 * silent duplicate. The caller surfaces this as a normal failed turn instead — same UX, and same
 * safety property, as the pre-existing manual "that message didn't go through, resend?" flow. */
export class StreamInterruptedError extends Error {
  constructor(reason: string) {
    super(`chat stream interrupted after progress began: ${reason}`);
    this.name = "StreamInterruptedError";
  }
}

interface StageEvent {
  type: "stage";
  stage: string;
  label: string;
}

interface DoneEvent {
  type: "done";
  payload: ChatResponse;
}

type ChatStreamEvent = StageEvent | DoneEvent;

async function* readFrames(body: ReadableStream<Uint8Array>) {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let boundary: number;
    while ((boundary = buffer.indexOf("\n\n")) !== -1) {
      const frame = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      const eventLine = frame.split("\n").find((line) => line.startsWith("event: "));
      const dataLine = frame.split("\n").find((line) => line.startsWith("data: "));
      if (eventLine && dataLine) {
        yield { event: eventLine.slice(7), data: JSON.parse(dataLine.slice(6)) as unknown };
      }
    }
  }
}

export async function* streamChat(
  message: string,
  threadId: string | undefined,
  signal: AbortSignal,
): AsyncGenerator<ChatStreamEvent, void, void> {
  let response: Response;
  try {
    response = await fetch("/api/chat/stream", {
      method: "POST",
      credentials: "same-origin",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(threadId ? { message, thread_id: threadId } : { message }),
      signal,
    });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") throw err;
    throw new StreamUnavailableError("network error opening the connection");
  }

  const contentType = response.headers.get("content-type") ?? "";
  if (!response.ok) {
    // A real HTTP failure before any bytes streamed (503 graph-unavailable, 401, etc.) — the
    // plain endpoint would fail identically, so this is a genuine error, not a transport gap.
    let detail = response.statusText;
    try {
      const body = (await response.json()) as { detail?: string };
      if (body?.detail) detail = body.detail;
    } catch {
      /* non-JSON error body — the status text stands */
    }
    throw new ApiError(response.status, detail);
  }
  if (!response.body || !contentType.startsWith("text/event-stream")) {
    // 200 but not actually a stream — a buffering proxy that swallowed the framing, most
    // plausibly. Retry through the plain transport rather than parsing garbage as SSE.
    throw new StreamUnavailableError(`unexpected content-type ${contentType || "(none)"}`);
  }

  let sawStageFrame = false;
  let sawTerminalFrame = false;
  try {
    for await (const { event, data } of readFrames(response.body)) {
      if (event === "stage") {
        sawStageFrame = true;
        const stage = data as { stage: string; label: string };
        yield { type: "stage", stage: stage.stage, label: stage.label };
      } else if (event === "done") {
        sawTerminalFrame = true;
        const parsed = ChatResponse.safeParse(data);
        if (!parsed.success) throw new ContractError("/api/chat/stream", parsed.error.issues);
        yield { type: "done", payload: parsed.data };
      } else if (event === "error") {
        sawTerminalFrame = true;
        const detail =
          (data as { detail?: string }).detail ??
          "the assistant is unavailable right now; please retry";
        // Framed as a 502 to match what the plain endpoint returns for the same graph failure
        // (PlanningError/NarrationError) — the two transports report identical outcomes.
        throw new ApiError(502, detail);
      }
    }
  } catch (err) {
    // `ApiError`/`ContractError` are already-classified outcomes raised deliberately above —
    // let them through unchanged. Anything else here is a low-level transport failure (the
    // reader rejecting, malformed framing from a mangling proxy) that was never given a type,
    // and it needs the same before/after-progress classification the clean-close path below
    // gets, or the whole point of this module — never silently duplicate a turn — is lost for
    // exactly the failure mode (a connection that dies mid-stream) it exists to handle.
    if (err instanceof DOMException && err.name === "AbortError") throw err;
    if (err instanceof ApiError || err instanceof ContractError) throw err;
    if (sawStageFrame) throw new StreamInterruptedError(String(err));
    throw new StreamUnavailableError(`transport error before any progress: ${String(err)}`);
  }

  if (!sawTerminalFrame) {
    // The connection closed clean but without a done/error frame: exactly the ALB-buffering or
    // silently-dropped-connection failure mode §11 flags as unproven. Whether this is safe to
    // silently retry depends on whether the graph was ever observed running.
    if (sawStageFrame) {
      throw new StreamInterruptedError("connection closed after progress but before completion");
    }
    throw new StreamUnavailableError("connection closed without a terminal event");
  }
}
