"use client";

import { AlertCircle, PanelLeft, RefreshCw } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Composer } from "@/components/chat/composer";
import { ConversationSidebar } from "@/components/chat/conversation-sidebar";
import { Markdown } from "@/components/chat/markdown";
import { MessageBubble, ThinkingPanel } from "@/components/chat/message-bubble";
import { ModelPicker } from "@/components/chat/model-picker";
import { Button } from "@/components/ui/button";
import { useChat } from "@/hooks/use-chat";
import {
  type Providers,
  deleteConversation,
  getProviders,
  patchConversation,
} from "@/lib/chat";

export function ChatView() {
  const router = useRouter();
  const params = useSearchParams();
  const conversationId = params.get("c");

  const [providers, setProviders] = useState<Providers | null>(null);
  const [model, setModel] = useState<string | null>(null);
  const [think, setThink] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);

  const chat = useChat(conversationId);
  const bottomRef = useRef<HTMLDivElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const pinnedToBottom = useRef(true);

  useEffect(() => {
    getProviders()
      .then((data) => {
        setProviders(data);
        setModel((current) => current ?? data.default_chat_model);
      })
      .catch(() => setProviders(null));
  }, []);

  // Follow the stream, but stop fighting the user if they scroll up to read.
  const onScroll = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    pinnedToBottom.current =
      el.scrollHeight - el.scrollTop - el.clientHeight < 80;
  }, []);

  useEffect(() => {
    if (pinnedToBottom.current) {
      bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
    }
  }, [chat.messages, chat.draft?.content]);

  const selectConversation = (id: string | null) => {
    router.push(id ? `/chat?c=${id}` : "/chat");
    setSidebarOpen(false);
  };

  const handleSend = async (text: string) => {
    const id = await chat.send(text, {
      model: model ?? undefined,
      think: think && thinkSupported,
    });
    // A new conversation gets its id only once the stream starts.
    if (id && id !== conversationId) router.replace(`/chat?c=${id}`);
  };

  const activeTitle = useMemo(
    () => chat.conversations.find((c) => c.id === conversationId)?.title,
    [chat.conversations, conversationId],
  );

  const noModels =
    providers !== null && providers.models.every((m) => m.kind !== "chat");

  const selectedModel = providers?.models.find((m) => m.id === model) ?? null;
  // Default to allowing it until providers load, so the button does not flicker.
  const thinkSupported = selectedModel?.supports_thinking ?? true;

  return (
    <div className="flex h-full">
      <ConversationSidebar
        conversations={chat.conversations}
        activeId={conversationId}
        open={sidebarOpen}
        onClose={() => setSidebarOpen(false)}
        onSelect={selectConversation}
        onNew={() => selectConversation(null)}
        onRename={async (id, title) => {
          await patchConversation(id, { title });
          await chat.refreshConversations();
        }}
        onDelete={async (id) => {
          await deleteConversation(id);
          await chat.refreshConversations();
          if (id === conversationId) selectConversation(null);
        }}
      />

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center gap-2 border-b px-3 py-2">
          <Button
            variant="ghost"
            size="icon"
            className="size-8 lg:hidden"
            onClick={() => setSidebarOpen(true)}
            aria-label="Open conversation list"
          >
            <PanelLeft className="size-4" />
          </Button>

          <h1 className="min-w-0 flex-1 truncate text-sm font-medium">
            {activeTitle ?? "New chat"}
          </h1>

          {providers && (
            <ModelPicker
              models={providers.models}
              value={model}
              onChange={async (id) => {
                setModel(id);
                if (conversationId)
                  await patchConversation(conversationId, { model: id });
              }}
              disabled={chat.isStreaming}
            />
          )}
        </header>

        <div
          ref={scrollRef}
          onScroll={onScroll}
          className="flex-1 overflow-y-auto"
        >
          <div className="mx-auto max-w-3xl space-y-5 px-4 py-6">
            {chat.messages.length === 0 && !chat.draft && !chat.loading && (
              <div className="py-16 text-center">
                <h2 className="text-lg font-medium">What can I help with?</h2>
                <p className="text-muted-foreground mt-1 text-sm">
                  {noModels
                    ? "No chat model available — check that Ollama is running."
                    : "Running locally. Nothing leaves this machine unless you pick a cloud model."}
                </p>
              </div>
            )}

            {chat.messages.map((message) => (
              <MessageBubble
                key={message.id}
                message={message}
                disabled={chat.isStreaming}
                onEdit={(m, content) =>
                  chat.editAndResend(m, content, {
                    model: model ?? undefined,
                    think,
                  })
                }
              />
            ))}

            {chat.draft && (
              <div className="space-y-2">
                {chat.draft.thinking && (
                  <ThinkingPanel text={chat.draft.thinking} />
                )}
                <div className="bg-muted max-w-[85%] rounded-2xl px-4 py-2.5 text-sm">
                  {chat.draft.content ? (
                    <Markdown content={chat.draft.content} />
                  ) : (
                    <span className="text-muted-foreground inline-flex gap-1">
                      <Dot /> <Dot delay="150ms" /> <Dot delay="300ms" />
                    </span>
                  )}
                </div>
              </div>
            )}

            {chat.error && (
              <div className="border-destructive/40 bg-destructive/5 text-destructive flex items-start gap-2 rounded-lg border p-3 text-sm">
                <AlertCircle className="mt-0.5 size-4 shrink-0" />
                <p className="min-w-0 flex-1">{chat.error}</p>
              </div>
            )}

            {!chat.isStreaming &&
              chat.messages.some((m) => m.role === "assistant") && (
                <div className="flex justify-start">
                  <Button
                    variant="ghost"
                    size="sm"
                    className="gap-1.5 text-xs"
                    onClick={() =>
                      chat.regenerate({ model: model ?? undefined, think })
                    }
                  >
                    <RefreshCw className="size-3.5" />
                    Regenerate
                  </Button>
                </div>
              )}

            <div ref={bottomRef} />
          </div>
        </div>

        <Composer
          onSend={handleSend}
          onStop={chat.stop}
          isStreaming={chat.isStreaming}
          think={think && thinkSupported}
          onThinkChange={setThink}
          thinkSupported={thinkSupported}
          disabled={noModels}
        />
      </div>
    </div>
  );
}

function Dot({ delay = "0ms" }: { delay?: string }) {
  return (
    <span
      className="bg-muted-foreground/60 size-1.5 animate-bounce rounded-full"
      style={{ animationDelay: delay }}
    />
  );
}
