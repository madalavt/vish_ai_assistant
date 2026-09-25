"use client";

import {
  AlertCircle,
  FileText,
  Globe,
  Loader2,
  MoreHorizontal,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { type SourceDocument, isProcessing } from "@/lib/notebooks";
import { cn } from "@/lib/utils";

export function DocumentRow({
  document,
  included,
  onToggle,
  onReprocess,
  onDelete,
}: {
  document: SourceDocument;
  /** Unchecked sources are excluded from retrieval. */
  included: boolean;
  onToggle: (id: string, include: boolean) => void;
  onReprocess: (id: string) => void;
  onDelete: (id: string) => void;
}) {
  const processing = isProcessing(document);
  const failed = document.status === "error";
  const pages = document.source_meta?.page_count as number | undefined;

  return (
    <li
      className={cn(
        "flex items-start gap-3 rounded-lg border p-3 transition-opacity",
        !included && "opacity-55",
      )}
    >
      <input
        type="checkbox"
        checked={included}
        disabled={document.status !== "ready"}
        onChange={(e) => onToggle(document.id, e.target.checked)}
        aria-label={`Include ${document.title} when answering`}
        className="mt-1 size-3.5 shrink-0 accent-current disabled:opacity-40"
      />
      <span className="mt-0.5 shrink-0">
        {processing ? (
          <Loader2 className="text-muted-foreground size-4 animate-spin" />
        ) : failed ? (
          <AlertCircle className="text-destructive size-4" />
        ) : document.source_type === "url" ? (
          <Globe className="text-muted-foreground size-4" />
        ) : (
          <FileText className="text-muted-foreground size-4" />
        )}
      </span>

      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium">{document.title}</p>
        <p
          className={cn(
            "mt-0.5 text-xs",
            failed ? "text-destructive" : "text-muted-foreground",
          )}
        >
          {failed
            ? document.error
            : processing
              ? document.status === "pending"
                ? "Queued…"
                : "Reading and embedding…"
              : [
                  document.source_type.toUpperCase(),
                  pages ? `${pages} pages` : null,
                  `${document.chunk_count} chunks`,
                ]
                  .filter(Boolean)
                  .join(" · ")}
        </p>
      </div>

      <DropdownMenu>
        <DropdownMenuTrigger
          aria-label={`Actions for ${document.title}`}
          render={
            <Button variant="ghost" size="icon" className="size-7 shrink-0" />
          }
        >
          <MoreHorizontal className="size-4" />
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          <DropdownMenuItem
            disabled={processing}
            onClick={() => onReprocess(document.id)}
          >
            {failed ? "Retry" : "Re-process"}
          </DropdownMenuItem>
          <DropdownMenuItem
            variant="destructive"
            onClick={() => onDelete(document.id)}
          >
            Delete
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </li>
  );
}
