"use client";

import { MoreHorizontal, Plus, X } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import type { Conversation } from "@/lib/chat";
import { cn } from "@/lib/utils";

export function ConversationSidebar({
  conversations,
  activeId,
  open,
  onClose,
  onSelect,
  onNew,
  onRename,
  onDelete,
}: {
  conversations: Conversation[];
  activeId: string | null;
  open: boolean;
  onClose: () => void;
  onSelect: (id: string) => void;
  onNew: () => void;
  onRename: (id: string, title: string) => void;
  onDelete: (id: string) => void;
}) {
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");

  const commitRename = () => {
    if (renamingId && renameValue.trim())
      onRename(renamingId, renameValue.trim());
    setRenamingId(null);
  };

  return (
    <>
      {/* Backdrop, only while open on narrow screens */}
      {open && (
        <button
          aria-label="Close conversation list"
          className="fixed inset-0 z-30 bg-black/40 lg:hidden"
          onClick={onClose}
        />
      )}

      <aside
        className={cn(
          "bg-sidebar flex w-64 shrink-0 flex-col border-r",
          // Off-canvas below lg, static column at lg and up.
          "fixed inset-y-0 left-0 z-40 transition-transform lg:static lg:z-auto lg:translate-x-0",
          open ? "translate-x-0" : "-translate-x-full",
        )}
      >
        <div className="flex items-center gap-2 p-3">
          <Button
            onClick={onNew}
            size="sm"
            className="flex-1 justify-start gap-2"
          >
            <Plus className="size-4" />
            New chat
          </Button>
          <Button
            variant="ghost"
            size="icon"
            className="size-8 lg:hidden"
            onClick={onClose}
            aria-label="Close"
          >
            <X className="size-4" />
          </Button>
        </div>

        <nav className="flex-1 overflow-y-auto px-2 pb-3">
          {conversations.length === 0 ? (
            <p className="text-muted-foreground px-2 py-4 text-xs">
              No conversations yet.
            </p>
          ) : (
            <ul className="space-y-0.5">
              {conversations.map((conversation) => (
                <li key={conversation.id} className="group/item relative">
                  {renamingId === conversation.id ? (
                    <Input
                      value={renameValue}
                      onChange={(e) => setRenameValue(e.target.value)}
                      onBlur={commitRename}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") commitRename();
                        if (e.key === "Escape") setRenamingId(null);
                      }}
                      className="h-8 text-sm"
                      autoFocus
                    />
                  ) : (
                    <>
                      <button
                        onClick={() => onSelect(conversation.id)}
                        className={cn(
                          "w-full truncate rounded-md py-2 pr-8 pl-2 text-left text-sm transition-colors",
                          conversation.id === activeId
                            ? "bg-sidebar-accent text-sidebar-accent-foreground"
                            : "text-muted-foreground hover:bg-sidebar-accent/50 hover:text-foreground",
                        )}
                      >
                        {conversation.title}
                      </button>

                      <DropdownMenu>
                        <DropdownMenuTrigger
                          aria-label={`Actions for ${conversation.title}`}
                          render={
                            <Button
                              variant="ghost"
                              size="icon"
                              className="absolute top-1 right-1 size-7 opacity-0 group-hover/item:opacity-100 focus-visible:opacity-100 data-[state=open]:opacity-100"
                            />
                          }
                        >
                          <MoreHorizontal className="size-4" />
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="end">
                          <DropdownMenuItem
                            onClick={() => {
                              setRenamingId(conversation.id);
                              setRenameValue(conversation.title);
                            }}
                          >
                            Rename
                          </DropdownMenuItem>
                          <DropdownMenuItem
                            variant="destructive"
                            onClick={() => onDelete(conversation.id)}
                          >
                            Delete
                          </DropdownMenuItem>
                        </DropdownMenuContent>
                      </DropdownMenu>
                    </>
                  )}
                </li>
              ))}
            </ul>
          )}
        </nav>
      </aside>
    </>
  );
}
