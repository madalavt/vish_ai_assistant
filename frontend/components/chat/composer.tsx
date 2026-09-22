"use client";

import { ArrowUp, Brain, Square } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";

export function Composer({
  onSend,
  onStop,
  isStreaming,
  think,
  onThinkChange,
  disabled,
}: {
  onSend: (text: string) => void;
  onStop: () => void;
  isStreaming: boolean;
  think: boolean;
  onThinkChange: (value: boolean) => void;
  disabled?: boolean;
}) {
  const [value, setValue] = useState("");
  const ref = useRef<HTMLTextAreaElement>(null);

  // Grow with content up to a cap, so long prompts stay visible without
  // pushing the conversation off screen.
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  }, [value]);

  const submit = () => {
    const text = value.trim();
    if (!text || isStreaming || disabled) return;
    setValue("");
    onSend(text);
  };

  return (
    <div className="bg-background/95 sticky bottom-0 border-t backdrop-blur">
      <div className="mx-auto max-w-3xl px-4 py-3">
        <div className="bg-muted/40 focus-within:ring-ring rounded-2xl border p-2 focus-within:ring-1">
          <Textarea
            ref={ref}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={(e) => {
              // Enter sends; Shift+Enter makes a newline.
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                submit();
              }
            }}
            placeholder={disabled ? "No model available" : "Send a message…"}
            disabled={disabled}
            rows={1}
            className="max-h-50 min-h-10 resize-none border-0 bg-transparent shadow-none focus-visible:ring-0"
          />

          <div className="flex items-center justify-between gap-2 px-1 pt-1">
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => onThinkChange(!think)}
              className={cn(
                "h-8 gap-1.5 text-xs",
                think && "text-foreground bg-accent",
              )}
              title="Let the model reason before answering. Slower, better on hard questions."
            >
              <Brain className="size-3.5" />
              Thinking {think ? "on" : "off"}
            </Button>

            {isStreaming ? (
              <Button
                type="button"
                size="sm"
                variant="secondary"
                onClick={onStop}
                className="gap-1.5"
              >
                <Square className="size-3.5 fill-current" />
                Stop
              </Button>
            ) : (
              <Button
                type="button"
                size="icon"
                className="size-8 rounded-full"
                onClick={submit}
                disabled={!value.trim() || disabled}
                aria-label="Send message"
              >
                <ArrowUp className="size-4" />
              </Button>
            )}
          </div>
        </div>
        <p className="text-muted-foreground mt-1.5 text-center text-[11px]">
          Enter to send · Shift+Enter for a new line
        </p>
      </div>
    </div>
  );
}
