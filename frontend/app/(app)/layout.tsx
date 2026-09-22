import { AppNav } from "@/components/app-nav";

export default function AppLayout({ children }: LayoutProps<"/">) {
  return (
    <div className="flex min-h-dvh">
      <AppNav />
      {/* pb-20 clears the fixed mobile bottom bar */}
      <main className="min-w-0 flex-1 pb-20 md:pb-0">{children}</main>
    </div>
  );
}
