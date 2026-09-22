import { Badge } from "@/components/ui/badge";
import { getHealth } from "@/lib/api";

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-4 py-1 text-sm">
      <span className="text-muted-foreground">{label}</span>
      <span className="font-mono text-xs">{value}</span>
    </div>
  );
}

/**
 * Server component: renders the live backend health check.
 * This is the M0 end-to-end proof that the frontend reaches the API,
 * which reaches Postgres and Ollama.
 */
export async function HealthCard() {
  const health = await getHealth();

  if (!health) {
    return (
      <div className="rounded-lg border border-dashed p-4">
        <div className="flex items-center gap-2">
          <Badge variant="destructive">unreachable</Badge>
          <span className="text-sm font-medium">Backend is not responding</span>
        </div>
        <p className="text-muted-foreground mt-2 text-sm">
          Start it with{" "}
          <code className="bg-muted rounded px-1 py-0.5 text-xs">
            cd backend &amp;&amp; uv run uvicorn app.main:app --reload
          </code>
        </p>
      </div>
    );
  }

  const { checks, config } = health;

  return (
    <div className="space-y-4 rounded-lg border p-4">
      <div className="flex items-center gap-2">
        <Badge variant={health.status === "ok" ? "default" : "secondary"}>{health.status}</Badge>
        <span className="text-sm font-medium">System</span>
      </div>

      <div className="divide-y">
        <div className="pb-2">
          <Row
            label="Postgres"
            value={
              checks.database.ok
                ? `${checks.database.server_version?.split(" ")[0]} · pgvector ${checks.database.pgvector}`
                : (checks.database.detail ?? "unavailable")
            }
          />
          <Row
            label="Ollama"
            value={
              checks.ollama.ok
                ? `${checks.ollama.version} · ${checks.ollama.models?.length ?? 0} models`
                : (checks.ollama.detail ?? "unavailable")
            }
          />
        </div>
        <div className="pt-2">
          <Row label="Chat model" value={config.default_chat_model} />
          <Row label="Embeddings" value={`${config.embedding_model} (${config.embedding_dim}d)`} />
          <Row label="Cloud" value={config.cloud_enabled ? (config.cloud_model ?? "on") : "off"} />
        </div>
      </div>
    </div>
  );
}
