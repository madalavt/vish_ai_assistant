"use client";

import { Check, ChevronsUpDown, Cloud, HardDrive, Wrench } from "lucide-react";
import type { ReactNode } from "react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import type { ModelInfo } from "@/lib/chat";
import { cn } from "@/lib/utils";

/** Models below this are the "small and fast" tier. */
const SMALL_MODEL_BYTES = 4e9;

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

  const sections: { key: string; label: ReactNode; models: ModelInfo[]; empty?: ReactNode }[] = [
    {
      key: "small",
      label: "Small & fast",
      models: chat.filter((m) => m.local && (m.size_bytes ?? 0) < SMALL_MODEL_BYTES),
    },
    {
      key: "large",
      label: "Larger, better",
      models: chat.filter((m) => m.local && (m.size_bytes ?? 0) >= SMALL_MODEL_BYTES),
    },
    {
      key: "cloud",
      label: (
        <>
          Cloud{" "}
          {chat.every((m) => m.local) && <span className="font-normal">— no API key set</span>}
        </>
      ),
      models: chat.filter((m) => !m.local),
      empty: (
        <p className="text-muted-foreground px-2 py-1.5 text-xs">
          Add ANTHROPIC_API_KEY to .env to use Claude.
        </p>
      ),
    },
  ];

  const visible = sections.filter((s) => s.models.length > 0 || s.empty);

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        render={<Button variant="outline" size="sm" className="gap-1.5" disabled={disabled} />}
      >
        {selected?.local === false ? (
          <Cloud className="size-3.5" />
        ) : (
          <HardDrive className="size-3.5" />
        )}
        <span className="max-w-40 truncate">{selected?.label ?? value ?? "No model"}</span>
        <ChevronsUpDown className="size-3.5 opacity-50" />
      </DropdownMenuTrigger>

      <DropdownMenuContent align="start" className="max-h-[70vh] w-80 overflow-y-auto">
        {visible.map((section, index) => (
          // Base UI requires a label to live inside a Group; rendering
          // DropdownMenuLabel directly under Content throws at runtime.
          <DropdownMenuGroup key={section.key}>
            {index > 0 && <DropdownMenuSeparator />}
            <DropdownMenuLabel className="text-xs">{section.label}</DropdownMenuLabel>
            {section.models.length > 0
              ? section.models.map((model) => (
                  <ModelRow
                    key={model.id}
                    model={model}
                    selected={model.id === value}
                    onSelect={onChange}
                  />
                ))
              : section.empty}
          </DropdownMenuGroup>
        ))}
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
    <DropdownMenuItem onClick={() => onSelect(model.id)} className="items-start gap-2">
      <Check className={cn("mt-0.5 size-4 shrink-0", selected ? "opacity-100" : "opacity-0")} />
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline gap-1.5">
          <p className="truncate text-sm">{model.label}</p>
          {model.supports_tools && (
            <Wrench className="text-muted-foreground size-3 shrink-0" aria-label="supports tools" />
          )}
        </div>
        <p className="text-muted-foreground truncate text-xs">{describe(model)}</p>
      </div>
    </DropdownMenuItem>
  );
}

function describe(model: ModelInfo): string {
  const parts = [model.id];

  if (model.local && model.size_bytes) {
    parts.push(`${(model.size_bytes / 1e9).toFixed(1)} GB`);
  }
  if (model.context_window) {
    const k = model.context_window / 1000;
    parts.push(`${k >= 1000 ? `${(k / 1000).toFixed(0)}M` : `${k.toFixed(0)}k`} ctx`);
  }
  if (model.supports_thinking) parts.push("thinking");
  if (!model.local) parts.push("leaves this machine");

  return parts.join(" · ");
}
