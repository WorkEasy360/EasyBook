"use client";

import * as React from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/cn";
import type { ResolvedSection } from "@/lib/navigation";
import { SidebarNav } from "./sidebar";
import { OrgSwitcher } from "./org-switcher";
import { UserMenu } from "./user-menu";
import { IconButton } from "@/components/ui/button";
import { Icons } from "@/components/ui/icons";
import type { Organization, User } from "@/types/api/accounts";

/**
 * Application chrome: fixed sidebar on desktop, off-canvas drawer below lg
 * (spec §11, §67).
 *
 * This is a Client Component because the drawer, the switcher and the menus
 * are interactive — but it takes its content as `children`, so the PAGES it
 * wraps stay Server Components and their data never travels through the
 * client bundle (spec §75).
 */

export function AppShell({
  sections,
  user,
  organizations,
  organization,
  children,
}: {
  sections: ResolvedSection[];
  user: User;
  organizations: Organization[];
  organization: Organization;
  children: React.ReactNode;
}) {
  const [drawerOpen, setDrawerOpen] = React.useState(false);
  const pathname = usePathname();
  const drawerRef = React.useRef<HTMLDialogElement>(null);
  const openerRef = React.useRef<HTMLButtonElement>(null);

  /*
   * Close the drawer on navigation — otherwise it stays open over the page the
   * user just asked for. Adjusted during render rather than in an effect: an
   * effect would paint the new page with the drawer still covering it for a
   * frame (react-hooks/set-state-in-effect).
   */
  const [lastPath, setLastPath] = React.useState(pathname);
  if (lastPath !== pathname) {
    setLastPath(pathname);
    if (drawerOpen) setDrawerOpen(false);
  }

  /*
   * The drawer is a native modal <dialog>: showModal() gives the focus trap,
   * the inert background, Escape handling and the top layer from the platform
   * (the same reasoning as components/ui/dialog.tsx). On close, focus goes
   * back to the button that opened it, so a keyboard user is not dropped at
   * the top of the document.
   */
  React.useEffect(() => {
    const drawer = drawerRef.current;
    if (!drawer) return;
    if (drawerOpen && !drawer.open) {
      drawer.showModal();
    } else if (!drawerOpen && drawer.open) {
      drawer.close();
      openerRef.current?.focus();
    }
  }, [drawerOpen]);

  React.useEffect(() => {
    const drawer = drawerRef.current;
    if (!drawer) return;
    function onCancel(event: Event) {
      event.preventDefault();
      setDrawerOpen(false);
    }
    drawer.addEventListener("cancel", onCancel);
    return () => drawer.removeEventListener("cancel", onCancel);
  }, []);

  return (
    <div className="min-h-dvh">
      {/* First tab stop on every page. */}
      <a href="#main-content" className="skip-link">
        Skip to main content
      </a>

      {/* --- Desktop sidebar ------------------------------------------- */}
      <aside
        data-print="hide"
        className="fixed inset-y-0 left-0 z-30 hidden w-(--spacing-sidebar) flex-col border-r border-ink-200 bg-white lg:flex"
      >
        <div className="flex h-(--spacing-header) shrink-0 items-center border-b border-ink-200 px-3">
          <Wordmark />
        </div>
        <div className="border-b border-ink-200 px-2 py-2">
          <OrgSwitcher organizations={organizations} active={organization} />
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto">
          <SidebarNav sections={sections} />
        </div>
      </aside>

      {/* --- Mobile drawer --------------------------------------------- */}
      <dialog
        ref={drawerRef}
        aria-label="Navigation"
        data-print="hide"
        onClick={(event) => {
          // A click on the <dialog> box itself (not its panel) is the backdrop.
          if (event.target === drawerRef.current) setDrawerOpen(false);
        }}
        className="fixed inset-y-0 left-0 m-0 h-dvh max-h-dvh w-72 max-w-[85vw] border-r border-ink-200 bg-white p-0 backdrop:bg-scrim lg:hidden"
      >
        <div className="flex h-full flex-col">
          <div className="flex h-(--spacing-header) shrink-0 items-center justify-between border-b border-ink-200 px-3">
            <Wordmark />
            <IconButton
              label="Close navigation"
              icon={<Icons.close className="size-4" />}
              size="sm"
              onClick={() => setDrawerOpen(false)}
            />
          </div>
          <div className="border-b border-ink-200 px-2 py-2">
            <OrgSwitcher organizations={organizations} active={organization} />
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto">
            <SidebarNav sections={sections} onNavigate={() => setDrawerOpen(false)} />
          </div>
        </div>
      </dialog>

      {/* --- Main column ------------------------------------------------ */}
      <div className="lg:pl-(--spacing-sidebar)">
        <header
          data-print="hide"
          className="sticky top-0 z-20 flex h-(--spacing-header) items-center gap-2 border-b border-ink-200 bg-white/95 px-3 backdrop-blur-sm sm:px-4"
        >
          <IconButton
            ref={openerRef}
            label="Open navigation"
            icon={<Icons.menu className="size-5" />}
            size="sm"
            className="lg:hidden"
            aria-expanded={drawerOpen}
            onClick={() => setDrawerOpen(true)}
          />

          <div className="min-w-0 flex-1">
            <GlobalSearchEntry />
          </div>

          <UserMenu user={user} />
        </header>

        {/* tabIndex -1: the skip link must be able to move focus here, not just scroll. */}
        <main id="main-content" tabIndex={-1} className="min-w-0 focus:outline-none">
          {children}
        </main>
      </div>
    </div>
  );
}

function Wordmark() {
  return (
    <Link href="/dashboard" className="flex items-center gap-2 rounded-md px-1 py-1">
      <span
        aria-hidden="true"
        className="flex size-6 items-center justify-center rounded bg-brand-700 text-xs font-bold text-white"
      >
        E
      </span>
      <span className="text-sm font-semibold tracking-tight text-ink-900">EasyBook</span>
    </Link>
  );
}

/**
 * Search entry point.
 *
 * BACKEND CONTRACT BLOCKER: there is no cross-entity search endpoint —
 * documents/api/urls.py exposes /documents/search/ and nothing else does
 * (spec §63). Until one exists this navigates to document search rather than
 * pretending to search customers, invoices and bills.
 */
function GlobalSearchEntry() {
  return (
    <Link
      href="/documents?focus=search"
      className={cn(
        "flex h-8 w-full max-w-sm items-center gap-2 rounded-md border border-ink-200 bg-ink-50 px-2.5",
        "text-sm text-ink-500 transition-colors hover:border-ink-300 hover:bg-white",
      )}
    >
      <Icons.search className="size-4 shrink-0" />
      <span className="truncate">Search documents…</span>
    </Link>
  );
}
