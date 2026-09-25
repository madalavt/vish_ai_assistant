/**
 * Server-sent event parsing for POSTed streams.
 *
 * EventSource cannot POST, so frames are parsed by hand. The buffer is the
 * part worth getting right once: a network chunk can split a frame anywhere,
 * including mid-JSON, so a partial tail must be carried to the next read.
 *
 * Shared by the chat tab and notebook chat rather than duplicated.
 */

const BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export type SSEFrame = { event: string; data: unknown };

export type StreamOutcome =
  /** The server sent a terminal `done` or `error`. */
  | { kind: "complete" }
  /** The request was aborted by the caller. */
  | { kind: "aborted" }
  /** The connection closed with no terminal event — a restart or a dropped link. */
  | { kind: "truncated" }
  /** The request never produced a stream. */
  | { kind: "failed"; message: string };

/**
 * POST JSON and dispatch each SSE frame.
 *
 * `onFrame` returns true for a terminal event, which is how a clean finish is
 * told apart from a connection that simply stopped.
 */
export async function postEventStream(
  path: string,
  body: unknown,
  onFrame: (frame: SSEFrame) => boolean | void,
  signal?: AbortSignal,
): Promise<StreamOutcome> {
  let response: Response;
  try {
    response = await fetch(`${BASE_URL}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal,
    });
  } catch (error) {
    if ((error as Error)?.name === "AbortError") return { kind: "aborted" };
    return {
      kind: "failed",
      message: "Cannot reach the backend. Is it running on port 8000?",
    };
  }

  if (!response.ok || !response.body) {
    let message = `Request failed (${response.status})`;
    try {
      const parsed = await response.json();
      if (parsed?.detail) message = String(parsed.detail);
    } catch {
      // Non-JSON error body; the status is all we have.
    }
    return { kind: "failed", message };
  }

  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  let terminated = false;

  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += value;

      // Frames are separated by a blank line; the last piece may be partial.
      const frames = buffer.split("\n\n");
      buffer = frames.pop() ?? "";

      for (const frame of frames) {
        if (!frame.trim()) continue;

        let event = "message";
        const dataLines: string[] = [];
        for (const line of frame.split("\n")) {
          if (line.startsWith("event:")) event = line.slice(6).trim();
          else if (line.startsWith("data:"))
            dataLines.push(line.slice(5).trim());
        }
        if (!dataLines.length) continue;

        let data: unknown;
        try {
          data = JSON.parse(dataLines.join("\n"));
        } catch {
          continue; // a malformed frame is skipped, not fatal
        }

        if (onFrame({ event, data })) terminated = true;
      }
    }
  } catch (error) {
    if ((error as Error)?.name === "AbortError") return { kind: "aborted" };
    return {
      kind: "failed",
      message: `Stream interrupted: ${(error as Error).message}`,
    };
  } finally {
    reader.releaseLock();
  }

  return terminated ? { kind: "complete" } : { kind: "truncated" };
}
