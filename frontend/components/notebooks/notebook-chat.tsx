"use client";

import { ArrowUp, Loader2, Quote, Square, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { CitedAnswer } from "@/components/notebooks/cited-answer";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import {
  type Source,
  sourcePageLabel,
  streamNotebookChat,
} from "@/lib/notebooks";
import { cn } from "@/lib/utils";

type Turn = {
  question: string;
  answer: string;
  sources: Source[];
  error: string | null;
  streaming: boolean;
};

export function NotebookChat({
  notebookId,
  model,
  documentIds,
  availableCount,
  readyCount,
}: {
  notebookId: string;
  model: string | null;
  /** null means every source; a list restricts retrieval. */
  documentIds: string[] | null;
  /** Sources that are both finished processing and switched on. */
  availableCount: number;
  /** Sources that have finished processing, however they are toggled. */
  readyCount: number;
}) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [question, setQuestion] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [openSource, setOpenSource] = useState<Source | null>(null);
  const conversationId = useRef<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  // Switching notebooks resets this component by remounting it — the parent
  // keys it on notebookId. Clearing state in an effect would mean an extra
  // render pass showing the previous notebook's answers.

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turns]);

  useEffect(() => () => abortRef.current?.abort(), []);

  const ask = async () => {
    const text = question.trim();
    if (!text || streaming || availableCount === 0) return;

    setQuestion("");
    setStreaming(true);
    setOpenSource(null);

    const index = turns.length;
    setTurns((prev) => [
      ...prev,
      { question: text, answer: "", sources: [], error: null, streaming: true },
    ]);

    const patch = (update: Partial<Turn>) =>
      setTurns((prev) =>
        prev.map((t, i) => (i === index ? { ...t, ...update } : t)),
      );

    const controller = new AbortController();
    abortRef.current = controller;

    await streamNotebookChat(
      notebookId,
      {
        conversation_id: conversationId.current,
        content: text,
        model,
        document_ids: documentIds,
      },
      {
        onStart: (event) => {
          conversationId.current = event.conversation_id;
        },
        onSources: (sources) => patch({ sources }),
        onToken: (chunk) =>
          setTurns((prev) =>
            prev.map((t, i) =>
              i === index ? { ...t, answer: t.answer + chunk } : t,
            ),
          ),
        onDone: () => patch({ streaming: false }),
        onError: (message) => patch({ error: message, streaming: false }),
      },
      controller.signal,
    );

    patch({ streaming: false });
    abortRef.current = null;
    setStreaming(false);
  };

  const stop = () => {
    abortRef.current?.abort();
    abortRef.current = null;
    setStreaming(false);
    setTurns((prev) =>
      prev.map((t) => (t.streaming ? { ...t, streaming: false } : t)),
    );
  };

  return (
    <div className="flex gap-4">
      <div className="min-w-0 flex-1 space-y-4">
        {turns.length === 0 && (
          <p className="text-muted-foreground text-sm">
            {readyCount === 0
              ? "Add a source and wait for it to finish processing, then ask a question here."
              : availableCount === 0
                ? "Every source is switched off. Tick at least one to ask a question."
                : "Ask a question and the answer will cite the passages it came from."}
          </p>
        )}

        {turns.map((turn, index) => (
          <div key={index} className="space-y-2">
            <p className="text-sm font-medium">{turn.question}</p>

            {turn.error ? (
              <p className="text-destructive text-sm">{turn.error}</p>
            ) : turn.answer ? (
              <CitedAnswer
                text={turn.answer}
                sources={turn.sources}
                activeNumber={openSource?.number ?? null}
                onCite={setOpenSource}
              />
            ) : (
              <p className="text-muted-foreground flex items-center gap-1.5 text-sm">
                <Loader2 className="size-3.5 animate-spin" />
                {turn.sources.length
                  ? `Reading ${turn.sources.length} passages…`
                  : "Searching your sources…"}
              </p>
            )}

            {!turn.streaming && turn.sources.length > 0 && (
              <div className="flex flex-wrap gap-1.5 pt-0.5">
                {turn.sources.map((source) => (
                  <button
                    key={source.chunk_id}
                    type="button"
                    onClick={() => setOpenSource(source)}
                    className={cn(
                      "rounded border px-1.5 py-0.5 text-[11px] transition-colors",
                      openSource?.chunk_id === source.chunk_id
                        ? "border-primary text-foreground"
                        : "text-muted-foreground hover:text-foreground",
                    )}
                  >
                    [{source.number}] {source.document_title.slice(0, 22)}
                    {sourcePageLabel(source)
                      ? ` ${sourcePageLabel(source)}`
                      : ""}
                  </button>
                ))}
              </div>
            )}
          </div>
        ))}

        <div ref={bottomRef} />

        <div className="bg-muted/40 focus-within:ring-ring rounded-xl border p-2 focus-within:ring-1">
          <Textarea
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void ask();
              }
            }}
            placeholder={
              readyCount === 0
                ? "No sources ready yet"
                : "Ask a question about your sources…"
            }
            disabled={readyCount === 0}
            rows={1}
            className="max-h-40 min-h-9 resize-none border-0 bg-transparent shadow-none focus-visible:ring-0"
          />
          <div className="flex justify-end pt-1">
            {streaming ? (
              <Button
                size="sm"
                variant="secondary"
                className="gap-1.5"
                onClick={stop}
              >
                <Square className="size-3.5 fill-current" />
                Stop
              </Button>
            ) : (
              <Button
                size="icon"
                className="size-8 rounded-full"
                disabled={!question.trim() || availableCount === 0}
                onClick={() => void ask()}
                aria-label="Ask"
              >
                <ArrowUp className="size-4" />
              </Button>
            )}
          </div>
        </div>
      </div>

      {openSource && (
        <aside className="hidden w-80 shrink-0 md:block">
          <div className="sticky top-4 rounded-lg border p-3">
            <div className="mb-2 flex items-start justify-between gap-2">
              <div className="min-w-0">
                <p className="flex items-center gap-1.5 text-xs font-medium">
                  <Quote className="size-3" />
                  Source [{openSource.number}]
                </p>
                <p className="text-muted-foreground mt-0.5 truncate text-xs">
                  {openSource.document_title}
                  {sourcePageLabel(openSource)
                    ? ` · ${sourcePageLabel(openSource)}`
                    : ""}
                  {openSource.section ? ` · ${openSource.section}` : ""}
                </p>
              </div>
              <Button
                variant="ghost"
                size="icon"
                className="size-6 shrink-0"
                onClick={() => setOpenSource(null)}
                aria-label="Close source"
              >
                <X className="size-3.5" />
              </Button>
            </div>
            <p className="text-muted-foreground max-h-96 overflow-y-auto text-xs leading-relaxed whitespace-pre-wrap">
              {openSource.content}
            </p>
            {/* Shown because it is the only signal of why this passage was
                retrieved — semantic match, keyword match, or both. */}
            <p className="text-muted-foreground mt-2 border-t pt-2 font-mono text-[10px]">
              rrf {openSource.score.toFixed(4)}
              {openSource.vector_rank
                ? ` · vec #${openSource.vector_rank}`
                : ""}
              {openSource.keyword_rank
                ? ` · kw #${openSource.keyword_rank}`
                : ""}
            </p>
          </div>
        </aside>
      )}
    </div>
  );
}
