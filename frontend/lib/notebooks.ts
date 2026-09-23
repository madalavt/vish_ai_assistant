/**
 * Notebooks API client. Shapes mirror `backend/app/schemas/notebook.py`.
 */

import { apiFetch } from "@/lib/api";

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
