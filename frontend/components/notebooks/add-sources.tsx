"use client";

import { Link2, Upload } from "lucide-react";
import { useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

export function AddSources({
  extensions,
  onFiles,
  onUrl,
  busy,
}: {
  extensions: string[];
  onFiles: (files: File[]) => void;
  onUrl: (url: string) => void;
  busy: boolean;
}) {
  const [dragging, setDragging] = useState(false);
  const [url, setUrl] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  const submitUrl = () => {
    const value = url.trim();
    if (!value) return;
    setUrl("");
    onUrl(value);
  };

  return (
    <div className="space-y-3">
      <div
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          const files = Array.from(e.dataTransfer.files);
          if (files.length) onFiles(files);
        }}
        className={cn(
          "rounded-lg border border-dashed p-6 text-center transition-colors",
          dragging ? "border-primary bg-accent/50" : "border-border",
        )}
      >
        <Upload className="text-muted-foreground mx-auto size-5" />
        <p className="mt-2 text-sm">Drop files here</p>
        <p className="text-muted-foreground mt-0.5 text-xs">
          {extensions.length
            ? extensions.join(" ")
            : "Loading supported types…"}
        </p>
        <Button
          variant="outline"
          size="sm"
          className="mt-3"
          disabled={busy}
          onClick={() => inputRef.current?.click()}
        >
          Choose files
        </Button>
        <input
          ref={inputRef}
          type="file"
          multiple
          accept={extensions.join(",")}
          className="hidden"
          onChange={(e) => {
            const files = Array.from(e.target.files ?? []);
            if (files.length) onFiles(files);
            e.target.value = "";
          }}
        />
      </div>

      <div className="flex gap-2">
        <Input
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && submitUrl()}
          placeholder="…or paste a URL"
          className="h-9"
        />
        <Button
          variant="outline"
          size="sm"
          className="h-9 gap-1.5"
          disabled={!url.trim() || busy}
          onClick={submitUrl}
        >
          <Link2 className="size-3.5" />
          Add
        </Button>
      </div>
    </div>
  );
}
