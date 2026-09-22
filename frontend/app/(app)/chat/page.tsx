import { HealthCard } from "@/components/health-card";
import { Placeholder } from "@/components/placeholder";

export default function ChatPage() {
  return (
    <Placeholder
      title="Chat"
      hint="Streaming conversation with a local model, or Claude when you need it."
      milestone="M2"
    >
      {/* The live health check is M0's end-to-end proof: browser -> API -> Postgres + Ollama. */}
      <HealthCard />
    </Placeholder>
  );
}
