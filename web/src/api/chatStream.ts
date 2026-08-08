/**
 * The SSE transport for one turn — DESIGN.md §6.10, §9.1, §11 "open risk".
 *
 * `POST /api/chat/stream` narrates a turn's stages as they happen instead of returning
 * everything at once. **Whether that works through the deployed ALB is unproven** (§11), so
 * this module treats the failure mode as a runtime signal rather than a fixed choice: any
 * connection that never reaches a `done`/`error` frame — a non-`event-stream` response, a
 * dropped connection, a proxy that buffers the whole body before releasing it — throws
 * `StreamUnavailableError`, and the caller (`AppScreen`) falls back to the plain,
 * already-tested `POST /api/chat` for that turn. A turn the graph itself failed (an `error`
 * frame) is a different thing and is NOT this error: that is a real turn failure and is
 * reported as one, the same way the plain endpoint's non-2xx would be.
 *
 * `EventSource` cannot send a POST body, so this hand-rolls the minimal SSE-over-`fetch` framing
 * the backend emits (`api/routers/chat.py`'s `_sse`): `event: <name>\ndata: <json>\n\n`.
 */

import { ApiError, ContractError } from "./client";
import { ChatResponse } from "./schemas";

export class StreamUnavailableError extends Error {
  constructor(reason: string) {
    super(`chat stream unavailable: ${reason}`);
    this.name = "StreamUnavailableError";
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

  let sawTerminalFrame = false;
  for await (const { event, data } of readFrames(response.body)) {
    if (event === "stage") {
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

  if (!sawTerminalFrame) {
    // The connection closed clean but without a done/error frame: exactly the ALB-buffering or
    // silently-dropped-connection failure mode §11 flags as unproven. Not a graph error — the
    // graph may have finished — so this falls back rather than reporting a turn failure.
    throw new StreamUnavailableError("connection closed without a terminal event");
  }
}
