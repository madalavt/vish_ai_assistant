import type { ReactNode } from "react";

/**
 * Shown for tabs whose milestone has not been built yet. Naming the milestone
 * keeps the shell honest about what does and does not work.
 */
export function Placeholder({
  title,
  hint,
  milestone,
  children,
}: {
  title: string;
  hint: string;
  milestone: string;
  children?: ReactNode;
}) {
  return (
    <div className="mx-auto max-w-3xl px-4 py-8 md:py-12">
      <header className="mb-8">
        <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
        <p className="text-muted-foreground mt-1 text-sm">{hint}</p>
      </header>

      <div className="bg-muted/40 mb-8 rounded-lg border border-dashed p-4">
        <p className="text-sm">
          Not built yet — arrives in <span className="font-medium">{milestone}</span>. See{" "}
          <code className="bg-muted rounded px-1 py-0.5 text-xs">docs/PLAN.md</code>.
        </p>
      </div>

      {children}
    </div>
  );
}
