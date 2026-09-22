import { AppNav } from "@/components/app-nav";

export default function AppLayout({ children }: LayoutProps<"/">) {
  return (
    // Fixed viewport height: pages own their own scrolling, which chat needs
    // to keep the composer pinned while the transcript scrolls.
    <div className="flex h-dvh overflow-hidden">
      <AppNav />
      {/* pb-14 clears the fixed mobile bottom bar */}
      <main className="min-w-0 flex-1 overflow-hidden pb-14 md:pb-0">
        {children}
      </main>
    </div>
  );
}
