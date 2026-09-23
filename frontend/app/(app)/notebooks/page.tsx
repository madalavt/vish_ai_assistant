import { Suspense } from "react";

import { NotebooksView } from "./notebooks-view";

export default function NotebooksPage() {
  return (
    <Suspense
      fallback={<div className="text-muted-foreground p-6 text-sm">Loading notebooks…</div>}
    >
      <NotebooksView />
    </Suspense>
  );
}
