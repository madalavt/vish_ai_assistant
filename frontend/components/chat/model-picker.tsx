"use client";

import { Check, ChevronsUpDown, Cloud, HardDrive } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import type { ModelInfo } from "@/lib/chat";
import { cn } from "@/lib/utils";

export function ModelPicker({
  models,
  value,
  onChange,
  disabled,
}: {
  models: ModelInfo[];
  value: string | null;
  onChange: (id: string) => void;
  disabled?: boolean;
}) {
  const chat = models.filter((m) => m.kind === "chat");
  const selected = chat.find((m) => m.id === value);
  const local = chat.filter((m) => m.local);
  const cloud = chat.filter((m) => !m.local);

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        render={
          <Button
            variant="outline"
            size="sm"
            className="gap-1.5"
            disabled={disabled}
          />
        }
      >
        {selected?.local === false ? (
          <Cloud className="size-3.5" />
        ) : (
          <HardDrive className="size-3.5" />
        )}
        <span className="max-w-40 truncate">
          {selected?.label ?? value ?? "No model"}
        </span>
        <ChevronsUpDown className="size-3.5 opacity-50" />
      </DropdownMenuTrigger>

      <DropdownMenuContent align="start" className="w-72">
        <DropdownMenuLabel className="text-xs">
          On this machine
        </DropdownMenuLabel>
        {local.map((model) => (
          <ModelRow
            key={model.id}
            model={model}
            selected={model.id === value}
            onSelect={onChange}
          />
        ))}

        <DropdownMenuSeparator />
        <DropdownMenuLabel className="text-xs">
          Cloud{" "}
          {cloud.length === 0 && (
            <span className="font-normal">— no API key set</span>
          )}
        </DropdownMenuLabel>
        {cloud.length === 0 ? (
          <p className="text-muted-foreground px-2 py-1.5 text-xs">
            Add ANTHROPIC_API_KEY to .env to use Claude.
          </p>
        ) : (
          cloud.map((model) => (
            <ModelRow
              key={model.id}
              model={model}
              selected={model.id === value}
              onSelect={onChange}
            />
          ))
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function ModelRow({
  model,
  selected,
  onSelect,
}: {
  model: ModelInfo;
  selected: boolean;
  onSelect: (id: string) => void;
}) {
  return (
    <DropdownMenuItem onSelect={() => onSelect(model.id)} className="gap-2">
      <Check className={cn("size-4", selected ? "opacity-100" : "opacity-0")} />
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm">{model.label}</p>
        <p className="text-muted-foreground truncate text-xs">
          {model.id}
          {model.context_window
            ? ` · ${(model.context_window / 1000).toFixed(0)}k ctx`
            : ""}
          {!model.local && " · leaves this machine"}
        </p>
      </div>
    </DropdownMenuItem>
  );
}
