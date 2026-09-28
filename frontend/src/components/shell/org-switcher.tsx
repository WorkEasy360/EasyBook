"use client";

import * as React from "react";
import { cn } from "@/lib/cn";
import { Icons } from "@/components/ui/icons";
import { ROLE_LABELS } from "@/lib/authz/permissions";
import type { Organization } from "@/types/api/accounts";

/**
 * Active-organization switcher.
 *
 * Switching does a FULL navigation (window.location) rather than a client
 * route push. Every Server Component payload, every React Query cache and
 * every router cache entry is scoped to the previous tenant, and a soft
 * navigation would keep some of them (spec §100). A full load is slower by a
 * few hundred milliseconds and is the only way to be certain nothing from the
 * previous organization survives.
 *
 * Built as a disclosure (a button that shows a list of buttons), not an ARIA
 * listbox: a listbox promises arrow-key selection, and announcing a pattern
 * the keyboard cannot actually operate is worse than a plain one that works
 * with Tab. Escape closes it and returns focus to the trigger.
 */

export function OrgSwitcher({
  organizations,
  active,
}: {
  organizations: Organization[];
  active: Organization;
}) {
  const [open, setOpen] = React.useState(false);
  const [switching, setSwitching] = React.useState<string | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const containerRef = React.useRef<HTMLDivElement>(null);
  const triggerRef = React.useRef<HTMLButtonElement>(null);
  const panelId = React.useId();

  const close = React.useCallback((returnFocus: boolean) => {
    setOpen(false);
    if (returnFocus) triggerRef.current?.focus();
  }, []);

  useDismiss(containerRef, close, open);

  async function select(organization: Organization) {
    if (organization.id === active.id) {
      setOpen(false);
      return;
    }
    setSwitching(organization.id);
    setError(null);
    try {
      const response = await fetch("/api/auth/organization", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ organizationId: organization.id }),
      });
      if (!response.ok) {
        setError("Could not switch organization.");
        setSwitching(null);
        return;
      }
      // See the file header: a full load is the only way to guarantee no
      // cache entry from the previous tenant survives the switch.
      // eslint-disable-next-line @next/next/no-location-assign-relative-destination
      window.location.assign("/dashboard");
    } catch {
      setError("Could not switch organization.");
      setSwitching(null);
    }
  }

  // A single organization is not a choice; render it as a plain label.
  if (organizations.length <= 1) {
    return (
      <div className="min-w-0 px-2 py-1.5">
        <p className="truncate text-sm font-semibold text-ink-900">{active.name}</p>
        {active.role ? (
          <p className="truncate text-2xs text-ink-500">{ROLE_LABELS[active.role]}</p>
        ) : null}
      </div>
    );
  }

  return (
    <div ref={containerRef} className="relative">
      <button
        ref={triggerRef}
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        aria-controls={panelId}
        className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left transition-colors hover:bg-ink-100"
      >
        <span className="min-w-0 flex-1">
          <span className="sr-only">Organization: </span>
          <span className="block truncate text-sm font-semibold text-ink-900">{active.name}</span>
          {active.role ? (
            <span className="block truncate text-2xs text-ink-500">{ROLE_LABELS[active.role]}</span>
          ) : null}
        </span>
        <Icons.chevronDown className="size-3.5 shrink-0 text-ink-400" />
      </button>

      {open ? (
        <div
          id={panelId}
          className="absolute z-40 mt-1 max-h-80 w-full min-w-56 overflow-auto rounded-md border border-ink-200 bg-white py-1 shadow-overlay"
        >
          <p className="px-3 pt-1 pb-1.5 text-2xs font-medium tracking-wide text-ink-500 uppercase">
            Switch organization
          </p>
          {organizations.map((organization) => {
            const isActive = organization.id === active.id;
            return (
              <button
                key={organization.id}
                type="button"
                aria-current={isActive ? "true" : undefined}
                disabled={switching !== null}
                onClick={() => void select(organization)}
                className={cn(
                  "flex w-full items-center gap-2 px-3 py-2 text-left text-sm transition-colors",
                  isActive ? "bg-brand-50 text-brand-800" : "text-ink-800 hover:bg-ink-100",
                  switching !== null && "cursor-wait opacity-60",
                )}
              >
                <span className="min-w-0 flex-1">
                  <span className="block truncate">{organization.name}</span>
                  {organization.role ? (
                    <span className="block truncate text-2xs text-ink-500">
                      {ROLE_LABELS[organization.role]}
                    </span>
                  ) : null}
                </span>
                {isActive ? <Icons.check className="size-4 shrink-0 text-brand-700" /> : null}
              </button>
            );
          })}
          {error ? (
            <p role="alert" className="px-3 py-2 text-xs text-danger-600">
              {error}
            </p>
          ) : null}
        </div>
      ) : null}

    </div>
  );
}

/**
 * Closes on outside click and on Escape — expected of any popover. Escape
 * returns focus to the trigger; an outside click leaves focus where the user
 * just clicked.
 */
function useDismiss(
  ref: React.RefObject<HTMLElement | null>,
  onDismiss: (returnFocus: boolean) => void,
  active: boolean,
) {
  React.useEffect(() => {
    if (!active) return;

    function onPointerDown(event: PointerEvent) {
      if (ref.current && !ref.current.contains(event.target as Node)) onDismiss(false);
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") onDismiss(true);
    }

    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [ref, onDismiss, active]);
}
