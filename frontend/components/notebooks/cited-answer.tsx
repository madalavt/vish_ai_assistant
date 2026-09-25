"use client";

import type { Source } from "@/lib/notebooks";
import { cn } from "@/lib/utils";

/** Matches the [1] / [2] markers the model emits. */
const CITATION = /\[(\d{1,2})\]/g;

/**
 * Render an answer with its [n] markers turned into clickable citations.
 *
 * Deliberately not markdown: the answer is short prose, and running it through
 * a markdown parser on every streamed token both costs re-parsing and makes it
 * far harder to splice interactive elements into the text safely.
 */
export function CitedAnswer({
  text,
  sources,
  activeNumber,
  onCite,
}: {
  text: string;
  sources: Source[];
  activeNumber: number | null;
  onCite: (source: Source) => void;
}) {
  const byNumber = new Map(sources.map((s) => [s.number, s]));
  const parts: React.ReactNode[] = [];

  let cursor = 0;
  let key = 0;
  for (const match of text.matchAll(CITATION)) {
    const index = match.index ?? 0;
    const number = Number(match[1]);
    const source = byNumber.get(number);

    if (index > cursor) parts.push(text.slice(cursor, index));

    if (source) {
      parts.push(
        <button
          key={`cite-${key++}`}
          type="button"
          onClick={() => onCite(source)}
          title={`${source.document_title}${source.section ? ` · ${source.section}` : ""}`}
          className={cn(
            "mx-0.5 inline-flex h-4 min-w-4 items-center justify-center rounded px-1 align-super text-[10px] font-medium transition-colors",
            activeNumber === number
              ? "bg-primary text-primary-foreground"
              : "bg-muted text-muted-foreground hover:bg-accent hover:text-foreground",
          )}
        >
          {number}
        </button>,
      );
    } else {
      // A marker pointing at no source would be a dead link; drop it but keep
      // the surrounding prose readable.
      parts.push("");
    }
    cursor = index + match[0].length;
  }

  if (cursor < text.length) parts.push(text.slice(cursor));

  return <p className="text-sm leading-relaxed whitespace-pre-wrap">{parts}</p>;
}
