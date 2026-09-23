"use client";

import { ChevronDown, Pencil } from "lucide-react";
import { useState } from "react";

import { CopyButton } from "@/components/chat/copy-button";
import { Markdown } from "@/components/chat/markdown";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import type { Message } from "@/lib/chat";
import { cn } from "@/lib/utils";

export function MessageBubble({
  message,
  onEdit,
  disabled,
}: {
  message: Message;
  onEdit?: (message: Message, content: string) => void;
  disabled?: boolean;
}) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(message.content);

  const isUser = message.role === "user";
  const failure = message.metadata?.error as string | undefined;
  const thinking = message.metadata?.thinking as string | undefined;

  if (editing) {
    return (
      <div className="flex justify-end">
        <div className="w-full max-w-[85%] space-y-2">
          <Textarea
            value={value}
            onChange={(e) => setValue(e.target.value)}
            className="min-h-24 resize-y"
            autoFocus
          />
          <div className="flex justify-end gap-2">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                setValue(message.content);
                setEditing(false);
              }}
            >
              Cancel
            </Button>
            <Button
              size="sm"
              disabled={!value.trim() || value === message.content}
              onClick={() => {
                setEditing(false);
                onEdit?.(message, value.trim());
              }}
            >
              Send
            </Button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div
      className={cn(
        "group flex flex-col gap-1",
        isUser ? "items-end" : "items-start",
      )}
    >
      {!isUser && thinking && <ThinkingPanel text={thinking} />}

      <div
        className={cn(
          "max-w-[85%] rounded-2xl px-4 py-2.5 text-sm",
          isUser ? "bg-primary text-primary-foreground" : "bg-muted",
        )}
      >
        {isUser ? (
          <p className="whitespace-pre-wrap break-words">{message.content}</p>
        ) : message.content ? (
          <Markdown content={message.content} />
        ) : (
          <p className="text-muted-foreground italic">No response</p>
        )}
      </div>

      {failure && (
        <p className="text-destructive max-w-[85%] text-xs">
          {message.content ? "Stream failed after partial output: " : ""}
          {failure}
        </p>
      )}

      <div className="flex items-center gap-1 opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100">
        {message.content && <CopyButton value={message.content} />}
        {isUser && onEdit && (
          <Button
            variant="ghost"
            size="sm"
            className="h-7 gap-1.5 px-2 text-xs"
            disabled={disabled}
            onClick={() => setEditing(true)}
          >
            <Pencil className="size-3.5" />
            Edit
          </Button>
        )}
        {!isUser && message.model && (
          <span className="text-muted-foreground px-1 text-[11px]">
            {message.model}
          </span>
        )}
      </div>
    </div>
  );
}

/** Collapsed reasoning output, shown only when the model emitted any. */
export function ThinkingPanel({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  if (!text.trim()) return null;

  return (
    <div className="max-w-[85%]">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="text-muted-foreground hover:text-foreground flex items-center gap-1 text-xs"
      >
        <ChevronDown
          className={cn("size-3.5 transition-transform", open && "rotate-180")}
        />
        Reasoning
      </button>
      {open && (
        <pre className="text-muted-foreground bg-muted/50 mt-1 max-h-64 overflow-auto rounded-md p-3 text-xs whitespace-pre-wrap">
          {text}
        </pre>
      )}
    </div>
  );
}
