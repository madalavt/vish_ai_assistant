/**
 * Notebooks API client. Shapes mirror `backend/app/schemas/notebook.py`.
 */

import { apiFetch } from "@/lib/api";
import { postEventStream } from "@/lib/sse";

const BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export type DocumentStatus = "pending" | "processing" | "ready" | "error";

export type SourceDocument = {
  id: string;
  notebook_id: string;
  title: string;
  source_type: string;
  status: DocumentStatus;
  error: string | null;
  chunk_count: number;
  source_meta: Record<string, unknown>;
  created_at: string;
  updated_at: string;
};

export type Notebook = {
  id: string;
  title: string;
  description: string | null;
  created_at: string;
  updated_at: string;
  document_count: number;
  ready_count: number;
};

export type NotebookDetail = Notebook & { documents: SourceDocument[] };

export type SearchHit = {
  chunk_id: string;
  document_id: string;
  document_title: string;
  content: string;
  similarity: number;
  page: number | null;
  end_page: number | null;
  section: string | null;
  start_char: number | null;
  end_char: number | null;
};

/** True while a document still needs polling. */
export const isProcessing = (d: SourceDocument) =>
  d.status === "pending" || d.status === "processing";

/** "p31" or "p31–32", matching what the backend actually recorded. */
export function pageLabel(hit: SearchHit): string | null {
  if (hit.page === null) return null;
  if (hit.end_page === null || hit.end_page === hit.page) return `p${hit.page}`;
  return `p${hit.page}–${hit.end_page}`;
}

export const listNotebooks = () => apiFetch<Notebook[]>("/api/notebooks");

export const getNotebook = (id: string) =>
  apiFetch<NotebookDetail>(`/api/notebooks/${id}`);

export const createNotebook = (title: string, description?: string) =>
  apiFetch<Notebook>("/api/notebooks", {
    method: "POST",
    body: JSON.stringify({ title, description: description ?? null }),
  });

export const patchNotebook = (
  id: string,
  patch: { title?: string; description?: string },
) =>
  apiFetch<Notebook>(`/api/notebooks/${id}`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });

export const listDocuments = (notebookId: string) =>
  apiFetch<SourceDocument[]>(`/api/notebooks/${notebookId}/documents`);

export const addUrlDocument = (notebookId: string, url: string) =>
  apiFetch<SourceDocument>(`/api/notebooks/${notebookId}/documents/url`, {
    method: "POST",
    body: JSON.stringify({ url }),
  });

export const reprocessDocument = (documentId: string) =>
  apiFetch<SourceDocument>(`/api/documents/${documentId}/reprocess`, {
    method: "POST",
  });

export const searchNotebook = (notebookId: string, query: string, limit = 10) =>
  apiFetch<{ query: string; hits: SearchHit[] }>(
    `/api/notebooks/${notebookId}/search`,
    {
      method: "POST",
      body: JSON.stringify({ query, limit }),
    },
  );

export const getSupportedExtensions = () =>
  apiFetch<{ extensions: string[] }>("/api/documents/supported/extensions");

async function expectNoContent(
  response: Response,
  what: string,
): Promise<void> {
  if (!response.ok)
    throw new Error(`Failed to delete ${what} (${response.status})`);
}

export async function deleteNotebook(id: string): Promise<void> {
  await expectNoContent(
    await fetch(`${BASE_URL}/api/notebooks/${id}`, { method: "DELETE" }),
    "notebook",
  );
}

export async function deleteDocument(id: string): Promise<void> {
  await expectNoContent(
    await fetch(`${BASE_URL}/api/documents/${id}`, { method: "DELETE" }),
    "document",
  );
}

/**
 * Upload a file. Uses fetch directly rather than apiFetch, which sets a JSON
 * content type — multipart needs the browser to set its own boundary.
 */
export async function uploadDocument(
  notebookId: string,
  file: File,
): Promise<SourceDocument> {
  const form = new FormData();
  form.append("file", file);

  const response = await fetch(
    `${BASE_URL}/api/notebooks/${notebookId}/documents`,
    {
      method: "POST",
      body: form,
    },
  );

  if (!response.ok) {
    let detail = `Upload failed (${response.status})`;
    try {
      const body = await response.json();
      if (body?.detail) detail = String(body.detail);
    } catch {
      // Non-JSON error body; the status is all we have.
    }
    throw new Error(detail);
  }

  return response.json() as Promise<SourceDocument>;
}

// ---------------------------------------------------------------------------
// Grounded chat (M4). Mirrors `backend/app/schemas/notebook.py`.
// ---------------------------------------------------------------------------

export type Source = {
  /** 1-based, matching the [n] markers in the answer. */
  number: number;
  chunk_id: string;
  document_id: string;
  document_title: string;
  content: string;
  page: number | null;
  end_page: number | null;
  section: string | null;
  start_char: number | null;
  end_char: number | null;
  score: number;
  vector_rank: number | null;
  keyword_rank: number | null;
};

export type NotebookChatStart = {
  conversation_id: string;
  user_message_id: string;
  assistant_message_id: string;
  model: string;
  title: string;
};

export type NotebookChatHandlers = {
  onStart?: (event: NotebookChatStart) => void;
  onSources?: (sources: Source[]) => void;
  onToken?: (text: string) => void;
  onDone?: (stopReason: string | null) => void;
  onError?: (message: string) => void;
};

/** "p31" or "p31–32", matching what was actually recorded. */
export function sourcePageLabel(source: Source): string | null {
  if (source.page === null) return null;
  if (source.end_page === null || source.end_page === source.page)
    return `p${source.page}`;
  return `p${source.page}–${source.end_page}`;
}

export async function streamNotebookChat(
  notebookId: string,
  request: {
    conversation_id?: string | null;
    content: string;
    model?: string | null;
    /** Omit for every source; a list restricts retrieval to those documents. */
    document_ids?: string[] | null;
  },
  handlers: NotebookChatHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const outcome = await postEventStream(
    `/api/notebooks/${notebookId}/chat/stream`,
    request,
    ({ event, data }) => {
      switch (event) {
        case "start":
          handlers.onStart?.(data as NotebookChatStart);
          return false;
        case "sources":
          handlers.onSources?.((data as { sources: Source[] }).sources);
          return false;
        case "token":
          handlers.onToken?.((data as { text: string }).text);
          return false;
        case "done":
          handlers.onDone?.(
            (data as { stop_reason: string | null }).stop_reason,
          );
          return true;
        case "error":
          handlers.onError?.((data as { message: string }).message);
          return true;
        default:
          return false;
      }
    },
    signal,
  );

  if (outcome.kind === "failed") handlers.onError?.(outcome.message);
  else if (outcome.kind === "truncated") {
    handlers.onError?.("The connection closed before the answer finished.");
  }
}
