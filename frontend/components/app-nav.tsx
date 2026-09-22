"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { TABS } from "@/lib/nav";
import { cn } from "@/lib/utils";

/**
 * Sidebar on md+, bottom bar on phones. Both render the same three tabs from
 * a single source so they cannot drift apart.
 */
export function AppNav() {
  const pathname = usePathname();
  const isActive = (href: string) =>
    pathname === href || pathname.startsWith(`${href}/`);

  return (
    <>
      {/* Desktop */}
      <nav
        aria-label="Main"
        className="hidden w-56 shrink-0 flex-col gap-1 border-r bg-sidebar p-3 md:flex"
      >
        <div className="px-2 pb-4">
          <p className="text-sm font-semibold">Assistant</p>
          <p className="text-muted-foreground text-xs">Running locally</p>
        </div>
        {TABS.map((tab) => (
          <Link
            key={tab.href}
            href={tab.href}
            aria-current={isActive(tab.href) ? "page" : undefined}
            className={cn(
              "rounded-md px-3 py-2 text-sm transition-colors",
              isActive(tab.href)
                ? "bg-sidebar-accent text-sidebar-accent-foreground font-medium"
                : "text-muted-foreground hover:bg-sidebar-accent/50 hover:text-foreground",
            )}
          >
            {tab.label}
          </Link>
        ))}
      </nav>

      {/* Mobile: fixed bottom bar, 44px+ touch targets */}
      <nav
        aria-label="Main"
        className="bg-background fixed inset-x-0 bottom-0 z-50 flex border-t md:hidden"
        style={{ paddingBottom: "env(safe-area-inset-bottom)" }}
      >
        {TABS.map((tab) => (
          <Link
            key={tab.href}
            href={tab.href}
            aria-current={isActive(tab.href) ? "page" : undefined}
            className={cn(
              "flex min-h-14 flex-1 flex-col items-center justify-center gap-0.5 text-xs transition-colors",
              isActive(tab.href)
                ? "text-foreground font-medium"
                : "text-muted-foreground active:bg-accent",
            )}
          >
            {tab.label}
          </Link>
        ))}
      </nav>
    </>
  );
}
