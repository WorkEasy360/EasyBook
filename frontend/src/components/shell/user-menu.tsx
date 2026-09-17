"use client";

import * as React from "react";
import Link from "next/link";
import { cn } from "@/lib/cn";
import { Icons } from "@/components/ui/icons";
import { displayName, initialsOf, type User } from "@/types/api/accounts";

/**
 * Account menu. A disclosure rather than an ARIA menu: the menu pattern
 * commits to arrow-key navigation and typeahead, and two plain items reached
 * with Tab serve a keyboard user better than a half-implemented menu. Escape
 * closes it and puts focus back on the trigger.
 */
export function UserMenu({ user }: { user: User }) {
  const [open, setOpen] = React.useState(false);
  const [signingOut, setSigningOut] = React.useState(false);
  const containerRef = React.useRef<HTMLDivElement>(null);
  const triggerRef = React.useRef<HTMLButtonElement>(null);
  const panelId = React.useId();

  React.useEffect(() => {
    if (!open) return;
    function onPointerDown(event: PointerEvent) {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setOpen(false);
        triggerRef.current?.focus();
      }
    }
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  async function signOut() {
    setSigningOut(true);
    try {
      await fetch("/api/auth/logout", { method: "POST" });
    } finally {
      // A hard navigation, not router.push: it discards every cached Server
      // Component payload along with the session.
      // eslint-disable-next-line @next/next/no-location-assign-relative-destination
      window.location.assign("/login");
    }
  }

  return (
    <div ref={containerRef} className="relative">
      <button
        ref={triggerRef}
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        aria-controls={panelId}
        className="flex items-center gap-2 rounded-md p-1 transition-colors hover:bg-ink-100"
      >
        <span
          aria-hidden="true"
          className="flex size-7 shrink-0 items-center justify-center rounded-full bg-brand-100 text-2xs font-semibold text-brand-800"
        >
          {initialsOf(user)}
        </span>
        <span className="sr-only">Account menu for {displayName(user)}</span>
        <Icons.chevronDown className="size-3.5 text-ink-400" />
      </button>

      {open ? (
        <div
          id={panelId}
          className="absolute right-0 z-40 mt-1 w-56 rounded-md border border-ink-200 bg-white py-1 shadow-overlay"
        >
          <div className="border-b border-ink-200 px-3 py-2">
            <p className="truncate text-sm font-medium text-ink-900">{displayName(user)}</p>
            <p className="truncate text-xs text-ink-500">{user.email}</p>
          </div>

          <Link
            href="/settings"
            onClick={() => setOpen(false)}
            className="flex items-center gap-2 px-3 py-2 text-sm text-ink-800 hover:bg-ink-100"
          >
            <Icons.settings className="size-4 text-ink-500" />
            Settings
          </Link>

          <button
            type="button"
            disabled={signingOut}
            onClick={() => void signOut()}
            className={cn(
              "flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-ink-800 hover:bg-ink-100",
              signingOut && "cursor-wait opacity-60",
            )}
          >
            <Icons.logout className="size-4 text-ink-500" />
            {signingOut ? "Signing out…" : "Sign out"}
          </button>
        </div>
      ) : null}
    </div>
  );
}
