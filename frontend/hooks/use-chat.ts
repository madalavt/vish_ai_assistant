"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import {
  type Conversation,
  type Message,
  getConversation,
  listConversations,
  streamChat,
  truncateConversation,
} from "@/lib/chat";

/** A message being streamed has no database row of its own yet. */
type Draft = { id: string; content: string; thinking: string };

export type UseChat = ReturnType<typeof useChat>;

/**
 * Chat state for one conversation.
 *
 * Deliberately hand-rolled rather than using the Vercel AI SDK's useChat: the
 * backend is Python and owns the wire format, so matching an evolving
 * TypeScript protocol would be the tail wagging the dog.
 */
export function useChat(conversationId: string | null) {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [messages, setMessages] = useState<Message[]>([]);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [isStreaming, setIsStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Derived rather than stored: a conversation is loading until its messages
  // have arrived. Storing it would mean a synchronous setState in the effect.
  const [loadedFor, setLoadedFor] = useState<string | null>(null);

  const abortRef = useRef<AbortController | null>(null);

  const refreshConversations = useCallback(async () => {
    try {
      setConversations(await listConversations());
    } catch {
      // The sidebar is not worth surfacing an error for; the composer will.
    }
  }, []);

  useEffect(() => {
    listConversations()
      .then(setConversations)
      .catch(() => {
        // The sidebar is not worth surfacing an error for; the composer will.
      });
  }, []);

  // Load the selected conversation. `cancelled` guards against a fast switch
  // resolving out of order and showing the wrong thread.
  useEffect(() => {
    let cancelled = false;

    // Resolve.then rather than a bare call so state never updates during the
    // effect's synchronous phase.
    const load = conversationId
      ? getConversation(conversationId).then((detail) => detail.messages)
      : Promise.resolve<Message[]>([]);

    load
      .then((loaded) => {
        if (cancelled) return;
        setMessages(loaded);
        setError(null);
      })
      .catch(() => {
        if (!cancelled) setError("Could not load that conversation.");
      })
      .finally(() => {
        if (!cancelled) setLoadedFor(conversationId);
      });

    return () => {
      cancelled = true;
    };
  }, [conversationId]);

  const stop = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setIsStreaming(false);
  }, []);

  /**
   * Run a turn. `content` null regenerates the existing last turn.
   * Returns the conversation id, which the caller needs when one was created.
   */
  const run = useCallback(
    async (
      content: string | null,
      options: { model?: string; think?: boolean } = {},
    ): Promise<string | null> => {
      if (isStreaming) return conversationId;

      setError(null);
      setIsStreaming(true);

      const controller = new AbortController();
      abortRef.current = controller;

      // Show the user's message immediately; the real row arrives with `start`.
      const optimisticId = `pending-${Date.now()}`;
      if (content !== null) {
        setMessages((prev) => [
          ...prev,
          {
            id: optimisticId,
            role: "user",
            content,
            position: prev.length,
            model: null,
            metadata: {},
            created_at: new Date().toISOString(),
          },
        ]);
      }

      let resolvedId = conversationId;

      await streamChat(
        {
          conversation_id: conversationId,
          content,
          model: options.model,
          think: options.think ?? false,
        },
        {
          onStart: (event) => {
            resolvedId = event.conversation_id;
            setDraft({
              id: event.assistant_message_id,
              content: "",
              thinking: "",
            });
            if (event.user_message_id) {
              setMessages((prev) =>
                prev.map((m) =>
                  m.id === optimisticId
                    ? { ...m, id: event.user_message_id! }
                    : m,
                ),
              );
            }
          },
          onToken: (text) =>
            setDraft((prev) =>
              prev ? { ...prev, content: prev.content + text } : prev,
            ),
          onThinking: (text) =>
            setDraft((prev) =>
              prev ? { ...prev, thinking: prev.thinking + text } : prev,
            ),
          onDone: () => setDraft(null),
          onError: (message) => {
            setError(message);
            setDraft(null);
          },
        },
        controller.signal,
      );

      abortRef.current = null;
      setIsStreaming(false);

      // Refetch rather than trusting local state: the server persisted the
      // real rows, including a partial answer if the stream failed.
      if (resolvedId) {
        try {
          const detail = await getConversation(resolvedId);
          setMessages(detail.messages);
        } catch {
          // Keep what is on screen.
        }
      }
      void refreshConversations();
      return resolvedId;
    },
    [conversationId, isStreaming, refreshConversations],
  );

  const send = useCallback(
    (content: string, options?: { model?: string; think?: boolean }) =>
      run(content, options),
    [run],
  );

  /** Drop the last assistant turn and ask again. */
  const regenerate = useCallback(
    async (options?: { model?: string; think?: boolean }) => {
      if (!conversationId || isStreaming) return;
      const lastAssistant = [...messages]
        .reverse()
        .find((m) => m.role === "assistant");
      if (!lastAssistant) return;

      const remaining = await truncateConversation(
        conversationId,
        lastAssistant.position,
      );
      setMessages(remaining);
      await run(null, options);
    },
    [conversationId, isStreaming, messages, run],
  );

  /** Replace a user turn and drop everything after it. */
  const editAndResend = useCallback(
    async (
      message: Message,
      content: string,
      options?: { model?: string; think?: boolean },
    ) => {
      if (!conversationId || isStreaming) return;
      const remaining = await truncateConversation(
        conversationId,
        message.position,
      );
      setMessages(remaining);
      await run(content, options);
    },
    [conversationId, isStreaming, run],
  );

  // Abort an in-flight stream when the conversation changes or the page unmounts.
  useEffect(() => () => abortRef.current?.abort(), [conversationId]);

  return {
    conversations,
    messages,
    draft,
    isStreaming,
    loading: conversationId !== null && loadedFor !== conversationId,
    error,
    send,
    regenerate,
    editAndResend,
    stop,
    refreshConversations,
    setError,
  };
}
