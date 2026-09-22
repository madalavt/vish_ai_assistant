/**
 * Backend API client.
 *
 * Every call goes through `apiFetch` so the base URL, error shape and
 * credential handling live in one place. Server components can call these
 * directly; client components use them inside effects or actions.
 */

const BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE_URL}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
    cache: "no-store",
  });

  if (!response.ok) {
    throw new ApiError(`${init?.method ?? "GET"} ${path} failed`, response.status);
  }

  return response.json() as Promise<T>;
}

// ---------------------------------------------------------------------------
// Health
// ---------------------------------------------------------------------------

export type HealthCheck = {
  ok: boolean;
  detail: string | null;
  [key: string]: unknown;
};

export type Health = {
  status: "ok" | "degraded";
  checks: {
    database: HealthCheck & { server_version?: string; pgvector?: string | null };
    ollama: HealthCheck & { version?: string; models?: string[]; missing?: string[] };
  };
  config: {
    default_chat_model: string;
    deep_chat_model: string;
    embedding_model: string;
    embedding_dim: number;
    cloud_enabled: boolean;
    cloud_model: string | null;
  };
};

/** Returns null when the backend is unreachable, so the UI can say so plainly. */
export async function getHealth(): Promise<Health | null> {
  try {
    return await apiFetch<Health>("/api/health");
  } catch {
    return null;
  }
}
