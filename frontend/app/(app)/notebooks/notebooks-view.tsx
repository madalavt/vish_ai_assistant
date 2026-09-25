"use client";

import { AlertCircle, PanelLeft, Plus, X } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";

import { AddSources } from "@/components/notebooks/add-sources";
import { DocumentRow } from "@/components/notebooks/document-row";
import { NotebookChat } from "@/components/notebooks/notebook-chat";
import { SearchPanel } from "@/components/notebooks/search-panel";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  type Notebook,
  type SourceDocument,
  addUrlDocument,
  createNotebook,
  deleteDocument,
  deleteNotebook,
  getSupportedExtensions,
  isProcessing,
  listDocuments,
  listNotebooks,
  reprocessDocument,
  uploadDocument,
} from "@/lib/notebooks";
import { cn } from "@/lib/utils";

/** How often to re-check documents that are still being processed. */
const POLL_MS = 2000;

export function NotebooksView() {
  const router = useRouter();
  const params = useSearchParams();
  const notebookId = params.get("n");

  const [notebooks, setNotebooks] = useState<Notebook[]>([]);
  const [documents, setDocuments] = useState<SourceDocument[]>([]);
  const [extensions, setExtensions] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [newTitle, setNewTitle] = useState("");
  // Document ids the user has switched off. Empty means every source is used.
  const [excluded, setExcluded] = useState<Set<string>>(new Set());

  const refreshNotebooks = useCallback(async () => {
    try {
      setNotebooks(await listNotebooks());
    } catch {
      setError("Cannot reach the backend. Is it running on port 8000?");
    }
  }, []);

  const refreshDocuments = useCallback(async (id: string) => {
    try {
      setDocuments(await listDocuments(id));
    } catch {
      // Transient; the next poll will pick it up.
    }
  }, []);

  useEffect(() => {
    // Resolved in .then rather than called directly, so state never updates
    // during the effect's synchronous phase.
    listNotebooks()
      .then(setNotebooks)
      .catch(() =>
        setError("Cannot reach the backend. Is it running on port 8000?"),
      );
    getSupportedExtensions()
      .then((r) => setExtensions(r.extensions))
      .catch(() => setExtensions([]));
  }, []);

  useEffect(() => {
    let cancelled = false;
    const load = notebookId
      ? listDocuments(notebookId)
      : Promise.resolve<SourceDocument[]>([]);
    load
      .then((docs) => {
        if (!cancelled) setDocuments(docs);
      })
      .catch(() => {
        if (!cancelled) setDocuments([]);
      });
    return () => {
      cancelled = true;
    };
  }, [notebookId]);

  // Poll only while something is actually processing, so an idle notebook
  // makes no requests at all.
  const anyProcessing = useMemo(
    () => documents.some(isProcessing),
    [documents],
  );

  useEffect(() => {
    if (!notebookId || !anyProcessing) return;
    const timer = setInterval(() => {
      void refreshDocuments(notebookId);
      void refreshNotebooks();
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [notebookId, anyProcessing, refreshDocuments, refreshNotebooks]);

  const select = (id: string | null) => {
    router.push(id ? `/notebooks?n=${id}` : "/notebooks");
    setSidebarOpen(false);
  };

  const handleCreate = async () => {
    const title = newTitle.trim() || "Untitled notebook";
    setNewTitle("");
    try {
      const created = await createNotebook(title);
      await refreshNotebooks();
      select(created.id);
    } catch {
      setError("Could not create the notebook.");
    }
  };

  const handleFiles = async (files: File[]) => {
    if (!notebookId) return;
    setBusy(true);
    setError(null);
    for (const file of files) {
      try {
        await uploadDocument(notebookId, file);
      } catch (e) {
        setError((e as Error).message);
      }
    }
    setBusy(false);
    await refreshDocuments(notebookId);
  };

  const handleUrl = async (url: string) => {
    if (!notebookId) return;
    setBusy(true);
    setError(null);
    try {
      await addUrlDocument(notebookId, url);
      await refreshDocuments(notebookId);
    } catch {
      setError(
        "Could not add that URL. Check it starts with http:// or https://",
      );
    } finally {
      setBusy(false);
    }
  };

  const active = notebooks.find((n) => n.id === notebookId) ?? null;
  const readyCount = documents.filter((d) => d.status === "ready").length;

  // Only ready documents have chunks to retrieve, and only ticked ones should
  // be searched. Counting selection alone would let a still-processing document
  // look askable, and would report ready sources as "not ready" once unticked.
  const includedReady = documents.filter(
    (d) => d.status === "ready" && !excluded.has(d.id),
  );

  // null means "every source", which lets the backend skip the filter entirely.
  const includedIds =
    excluded.size === 0 ? null : includedReady.map((d) => d.id);

  return (
    <div className="flex h-full">
      {sidebarOpen && (
        <button
          aria-label="Close notebook list"
          className="fixed inset-0 z-30 bg-black/40 lg:hidden"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      <aside
        className={cn(
          "bg-sidebar flex w-64 shrink-0 flex-col border-r",
          "fixed inset-y-0 left-0 z-40 transition-transform lg:static lg:z-auto lg:translate-x-0",
          sidebarOpen ? "translate-x-0" : "-translate-x-full",
        )}
      >
        <div className="flex items-center gap-2 p-3">
          <Input
            value={newTitle}
            onChange={(e) => setNewTitle(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleCreate()}
            placeholder="New notebook…"
            className="h-8 text-sm"
          />
          <Button
            size="icon"
            className="size-8 shrink-0"
            onClick={handleCreate}
            aria-label="Create notebook"
          >
            <Plus className="size-4" />
          </Button>
          <Button
            variant="ghost"
            size="icon"
            className="size-8 lg:hidden"
            onClick={() => setSidebarOpen(false)}
            aria-label="Close"
          >
            <X className="size-4" />
          </Button>
        </div>

        <nav className="flex-1 overflow-y-auto px-2 pb-3">
          {notebooks.length === 0 ? (
            <p className="text-muted-foreground px-2 py-4 text-xs">
              No notebooks yet.
            </p>
          ) : (
            <ul className="space-y-0.5">
              {notebooks.map((notebook) => (
                <li key={notebook.id}>
                  <button
                    onClick={() => select(notebook.id)}
                    className={cn(
                      "w-full rounded-md px-2 py-2 text-left transition-colors",
                      notebook.id === notebookId
                        ? "bg-sidebar-accent text-sidebar-accent-foreground"
                        : "text-muted-foreground hover:bg-sidebar-accent/50 hover:text-foreground",
                    )}
                  >
                    <span className="block truncate text-sm">
                      {notebook.title}
                    </span>
                    <span className="text-muted-foreground block text-xs">
                      {notebook.document_count === 0
                        ? "No sources"
                        : `${notebook.ready_count}/${notebook.document_count} ready`}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </nav>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center gap-2 border-b px-3 py-2">
          <Button
            variant="ghost"
            size="icon"
            className="size-8 lg:hidden"
            onClick={() => setSidebarOpen(true)}
            aria-label="Open notebook list"
          >
            <PanelLeft className="size-4" />
          </Button>
          <h1 className="min-w-0 flex-1 truncate text-sm font-medium">
            {active?.title ?? "Notebooks"}
          </h1>
          {active && (
            <Button
              variant="ghost"
              size="sm"
              className="text-destructive text-xs"
              onClick={async () => {
                await deleteNotebook(active.id);
                await refreshNotebooks();
                select(null);
              }}
            >
              Delete notebook
            </Button>
          )}
        </header>

        <div className="flex-1 overflow-y-auto">
          <div className="mx-auto max-w-3xl space-y-6 px-4 py-6">
            {error && (
              <div className="border-destructive/40 bg-destructive/5 text-destructive flex items-start gap-2 rounded-lg border p-3 text-sm">
                <AlertCircle className="mt-0.5 size-4 shrink-0" />
                <p className="min-w-0 flex-1">{error}</p>
              </div>
            )}

            {!notebookId ? (
              <div className="py-16 text-center">
                <h2 className="text-lg font-medium">Notebooks</h2>
                <p className="text-muted-foreground mx-auto mt-1 max-w-md text-sm">
                  Upload PDFs, Word documents, notes or a web page, then search
                  across them by meaning. Create a notebook on the left to
                  start.
                </p>
              </div>
            ) : (
              <>
                <section>
                  <h2 className="mb-2 text-sm font-medium">Add sources</h2>
                  <AddSources
                    extensions={extensions}
                    onFiles={handleFiles}
                    onUrl={handleUrl}
                    busy={busy}
                  />
                </section>

                <section>
                  <h2 className="mb-2 text-sm font-medium">
                    Sources{" "}
                    <span className="text-muted-foreground font-normal">
                      ({documents.length})
                    </span>
                  </h2>
                  {documents.length === 0 ? (
                    <p className="text-muted-foreground text-sm">
                      Nothing added yet.
                    </p>
                  ) : (
                    <ul className="space-y-2">
                      {documents.map((document) => (
                        <DocumentRow
                          key={document.id}
                          document={document}
                          included={!excluded.has(document.id)}
                          onToggle={(id, include) =>
                            setExcluded((prev) => {
                              const next = new Set(prev);
                              if (include) next.delete(id);
                              else next.add(id);
                              return next;
                            })
                          }
                          onReprocess={async (id) => {
                            await reprocessDocument(id);
                            await refreshDocuments(notebookId);
                          }}
                          onDelete={async (id) => {
                            await deleteDocument(id);
                            await refreshDocuments(notebookId);
                            await refreshNotebooks();
                          }}
                        />
                      ))}
                    </ul>
                  )}
                </section>

                <section>
                  <h2 className="mb-2 text-sm font-medium">Ask</h2>
                  <NotebookChat
                    // Remounts on notebook change, so a previous notebook's
                    // answers and citations never linger.
                    key={notebookId}
                    notebookId={notebookId}
                    model={null}
                    documentIds={includedIds}
                    availableCount={includedReady.length}
                    readyCount={readyCount}
                  />
                </section>

                <details className="group">
                  <summary className="cursor-pointer text-sm font-medium">
                    Search the indexed passages
                  </summary>
                  <div className="mt-2">
                    <SearchPanel
                      notebookId={notebookId}
                      disabled={readyCount === 0}
                    />
                    <p className="text-muted-foreground mt-2 text-xs">
                      Raw retrieval, without an answer written over it. Useful
                      for checking what was actually indexed.
                    </p>
                  </div>
                </details>
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
