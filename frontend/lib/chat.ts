/**
 * Chat API client and SSE parsing.
 *
 * Event shapes mirror `backend/app/schemas/chat.py`. Keep them in sync.
 */

import { apiFetch } from "@/lib/api";

const BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export type Role = "user" | "assistant" | "system";

export type Message = {
  id: string;
  role: Role;
  content: string;
  position: number;
  model: string | null;
  metadata: Record<string, unknown>;
  created_at: string;
};

export type Conversation = {
  id: string;
  title: string;
  model: string;
  created_at: string;
  updated_at: string;
};

export type ConversationDetail = Conversation & { messages: Message[] };

export type ModelInfo = {
  id: string;
  label: string;
  provider: string;
  kind: "chat" | "embedding";
  context_window: number | null;
  local: boolean;
  parameter_size: string | null;
  size_bytes: number | null;
  quantization: string | null;
  family: string | null;
  /** Needed by the M5 workflow LLM node. */
  supports_tools: boolean;
  /** Gates the composer's thinking toggle. */
  supports_thinking: boolean;
};

export type Providers = {
  models: ModelInfo[];
  default_chat_model: string | null;
  embedding_model: string;
  cloud_available: boolean;
};

// ---------------------------------------------------------------------------
// REST
// ---------------------------------------------------------------------------

export const listConversations = () =>
  apiFetch<Conversation[]>("/api/conversations");

export const getConversation = (id: string) =>
  apiFetch<ConversationDetail>(`/api/conversations/${id}`);

export const patchConversation = (
  id: string,
  patch: { title?: string; model?: string },
) =>
  apiFetch<Conversation>(`/api/conversations/${id}`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });

export const truncateConversation = (id: string, position: number) =>
  apiFetch<Message[]>(`/api/conversations/${id}/truncate`, {
    method: "POST",
    body: JSON.stringify({ position }),
  });

export async function deleteConversation(id: string): Promise<void> {
  const response = await fetch(`${BASE_URL}/api/conversations/${id}`, {
    method: "DELETE",
  });
  if (!response.ok)
    throw new Error(`Failed to delete conversation (${response.status})`);
}

export const getProviders = () => apiFetch<Providers>("/api/providers");

// ---------------------------------------------------------------------------
// Streaming
// ---------------------------------------------------------------------------

export type StartEvent = {
  conversation_id: string;
  user_message_id: string | null;
  assistant_message_id: string;
  model: string;
  title: string;
};

export type StreamHandlers = {
  onStart?: (event: StartEvent) => void;
  onToken?: (text: string) => void;
  onThinking?: (text: string) => void;
  onDone?: (stopReason: string | null, usage: Record<string, unknown>) => void;
  onError?: (message: string) => void;
};

export type ChatRequest = {
  conversation_id?: string | null;
  /** null regenerates the last turn instead of adding a new one. */
  content?: string | null;
  model?: string | null;
  think?: boolean;
};

/**
 * POST a message and dispatch SSE events as they arrive.
 *
 * EventSource cannot POST, so frames are parsed by hand. The buffer matters:
 * a network chunk can split an event anywhere, including mid-JSON.
 */
export async function streamChat(
  request: ChatRequest,
  handlers: StreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  let response: Response;
  try {
    response = await fetch(`${BASE_URL}/api/chat/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
      signal,
    });
  } catch (error) {
    if ((error as Error)?.name === "AbortError") return;
    handlers.onError?.("Cannot reach the backend. Is it running on port 8000?");
    return;
  }

  if (!response.ok || !response.body) {
    let detail = `Request failed (${response.status})`;
    try {
      const body = await response.json();
      if (body?.detail) detail = String(body.detail);
    } catch {
      // Non-JSON error body; the status is enough.
    }
    handlers.onError?.(detail);
    return;
  }

  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  // A stream that closes without `done` or `error` was cut off, e.g. by a backend restart.
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

        let name = "message";
        const dataLines: string[] = [];
        for (const line of frame.split("\n")) {
          if (line.startsWith("event:")) name = line.slice(6).trim();
          else if (line.startsWith("data:"))
            dataLines.push(line.slice(5).trim());
        }
        if (!dataLines.length) continue;

        let payload: unknown;
        try {
          payload = JSON.parse(dataLines.join("\n"));
        } catch {
          continue;
        }

        switch (name) {
          case "start":
            handlers.onStart?.(payload as StartEvent);
            break;
          case "token":
            handlers.onToken?.((payload as { text: string }).text);
            break;
          case "thinking":
            handlers.onThinking?.((payload as { text: string }).text);
            break;
          case "done": {
            terminated = true;
            const done_ = payload as {
              stop_reason: string | null;
              usage: Record<string, unknown>;
            };
            handlers.onDone?.(done_.stop_reason, done_.usage ?? {});
            break;
          }
          case "error":
            terminated = true;
            handlers.onError?.((payload as { message: string }).message);
            break;
        }
      }
    }
    if (!terminated) {
      handlers.onError?.("The connection closed before the reply finished.");
    }
  } catch (error) {
    if ((error as Error)?.name !== "AbortError") {
      handlers.onError?.(`Stream interrupted: ${(error as Error).message}`);
    }
  } finally {
    reader.releaseLock();
  }
}
