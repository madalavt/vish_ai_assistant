"use client";

import { Search } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { type SearchHit, pageLabel, searchNotebook } from "@/lib/notebooks";

export function SearchPanel({
  notebookId,
  disabled,
}: {
  notebookId: string;
  disabled: boolean;
}) {
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<SearchHit[] | null>(null);
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = async () => {
    const text = query.trim();
    if (!text || searching) return;

    setSearching(true);
    setError(null);
    try {
      setHits((await searchNotebook(notebookId, text)).hits);
    } catch {
      setError("Search failed. Is the backend running?");
      setHits(null);
    } finally {
      setSearching(false);
    }
  };

  return (
    <div className="space-y-3">
      <div className="flex gap-2">
        <Input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && run()}
          placeholder={disabled ? "Add a source first" : "Search your sources…"}
          disabled={disabled}
          className="h-9"
        />
        <Button
          size="sm"
          className="h-9 gap-1.5"
          disabled={disabled || !query.trim() || searching}
          onClick={run}
        >
          <Search className="size-3.5" />
          {searching ? "Searching…" : "Search"}
        </Button>
      </div>

      {error && <p className="text-destructive text-sm">{error}</p>}

      {hits !== null && hits.length === 0 && !error && (
        <p className="text-muted-foreground text-sm">
          No matches. Try different wording — this searches meaning, not exact
          words.
        </p>
      )}

      {hits && hits.length > 0 && (
        <ul className="space-y-2">
          {hits.map((hit) => (
            <li key={hit.chunk_id} className="rounded-lg border p-3">
              <div className="mb-1.5 flex items-baseline justify-between gap-2">
                <p className="truncate text-xs font-medium">
                  {hit.document_title}
                  {pageLabel(hit) && (
                    <span className="text-muted-foreground font-normal">
                      {" "}
                      · {pageLabel(hit)}
                    </span>
                  )}
                  {hit.section && (
                    <span className="text-muted-foreground font-normal">
                      {" "}
                      · {hit.section}
                    </span>
                  )}
                </p>
                {/* Similarity is shown because it is the only signal of whether
                    a weak match is worth reading. */}
                <span className="text-muted-foreground shrink-0 font-mono text-[11px]">
                  {hit.similarity.toFixed(3)}
                </span>
              </div>
              <p className="text-muted-foreground line-clamp-4 text-xs leading-relaxed">
                {hit.content}
              </p>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
