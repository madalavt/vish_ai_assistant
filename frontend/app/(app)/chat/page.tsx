import { Suspense } from "react";

import { ChatView } from "./chat-view";

export default function ChatPage() {
  // useSearchParams needs a Suspense boundary during prerender.
  return (
    <Suspense
      fallback={
        <div className="p-6 text-sm text-muted-foreground">Loading chat…</div>
      }
    >
      <ChatView />
    </Suspense>
  );
}
