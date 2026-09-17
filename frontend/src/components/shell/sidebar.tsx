"use client";

import { appHref } from "@/lib/routes";
import * as React from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/cn";
import { isActiveHref, type ResolvedSection } from "@/lib/navigation";
import { Icons } from "@/components/ui/icons";
import { NavIcon } from "@/components/ui/nav-icon";

/**
 * Primary navigation.
 *
 * Renders only what the role can reach — the sections are already filtered
 * server-side by navigationForRole(), so nothing unauthorized is even sent to
 * the browser. A section auto-expands when the current route is inside it, so
 * a deep link arrives with its context open.
 */

export function SidebarNav({
  sections,
  onNavigate,
}: {
  sections: ResolvedSection[];
  onNavigate?: () => void;
}) {
  const pathname = usePathname();

  return (
    <nav aria-label="Main" className="flex flex-col gap-0.5 px-2 py-3">
      {sections.map((section) =>
        section.href ? (
          <SidebarLink
            key={section.label}
            href={section.href}
            label={section.label}
            icon={section.icon}
            active={isActiveHref(pathname, section.href)}
            onNavigate={onNavigate}
          />
        ) : (
          <SidebarGroup
            key={section.label}
            section={section}
            pathname={pathname}
            onNavigate={onNavigate}
          />
        ),
      )}
    </nav>
  );
}

function SidebarLink({
  href,
  label,
  icon,
  active,
  onNavigate,
}: {
  href: string;
  label: string;
  icon: ResolvedSection["icon"];
  active: boolean;
  onNavigate?: () => void;
}) {
  return (
    <Link
      href={appHref(href)}
      onClick={onNavigate}
      aria-current={active ? "page" : undefined}
      className={cn(
        "flex items-center gap-2.5 rounded-md px-2.5 py-2 text-sm transition-colors",
        active
          ? "bg-brand-50 font-medium text-brand-800"
          : "text-ink-700 hover:bg-ink-100 hover:text-ink-900",
      )}
    >
      <NavIcon name={icon} className={cn("size-4 shrink-0", active ? "text-brand-700" : "text-ink-500")} />
      <span className="truncate">{label}</span>
    </Link>
  );
}

function SidebarGroup({
  section,
  pathname,
  onNavigate,
}: {
  section: ResolvedSection;
  pathname: string;
  onNavigate?: () => void;
}) {
  const containsActive = section.items.some((item) => isActiveHref(pathname, item.href));

  /*
   * A section is open when it contains the current route, unless the user has
   * toggled it since the last navigation. Derived during render rather than
   * synced in an effect: an effect would paint the wrong state for one frame
   * and re-render immediately after (react-hooks/set-state-in-effect).
   */
  const [override, setOverride] = React.useState<boolean | null>(null);
  const [lastPath, setLastPath] = React.useState(pathname);
  if (lastPath !== pathname) {
    // React's documented "adjust state when a prop changes" pattern: a
    // navigation clears the manual override so the route decides again.
    setLastPath(pathname);
    setOverride(null);
  }
  const open = override ?? containsActive;

  const panelId = `nav-${section.label.toLowerCase().replace(/\s+/g, "-")}`;

  return (
    <div>
      <button
        type="button"
        onClick={() => setOverride(!open)}
        aria-expanded={open}
        aria-controls={panelId}
        className={cn(
          "flex w-full items-center gap-2.5 rounded-md px-2.5 py-2 text-sm transition-colors",
          containsActive ? "text-ink-900" : "text-ink-700",
          "hover:bg-ink-100",
        )}
      >
        <NavIcon
          name={section.icon}
          className={cn("size-4 shrink-0", containsActive ? "text-brand-700" : "text-ink-500")}
        />
        <span className="flex-1 truncate text-left font-medium">{section.label}</span>
        <Icons.chevronDown
          className={cn("size-3.5 shrink-0 text-ink-400 transition-transform", open && "rotate-180")}
        />
      </button>

      <div id={panelId} hidden={!open} className="mt-0.5 mb-1 ml-3 border-l border-ink-200 pl-2">
        {section.items.map((item) => {
          const active = isActiveHref(pathname, item.href);
          return (
            <Link
              key={item.href}
              href={appHref(item.href)}
              onClick={onNavigate}
              aria-current={active ? "page" : undefined}
              className={cn(
                "block rounded-md px-2.5 py-1.5 text-sm transition-colors",
                active
                  ? "bg-brand-50 font-medium text-brand-800"
                  : "text-ink-600 hover:bg-ink-100 hover:text-ink-900",
              )}
            >
              {item.label}
            </Link>
          );
        })}
      </div>
    </div>
  );
}
