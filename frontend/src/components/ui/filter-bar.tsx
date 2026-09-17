import { appHref } from "@/lib/routes";
import Link from "next/link";
import { cn } from "@/lib/cn";

/**
 * List filters, rendered as links.
 *
 * A Server Component: each option is an <a> to the same page with one query
 * param changed, so filtering needs no client JavaScript at all and every
 * filtered view is a real, shareable URL (spec §66, §75).
 *
 * There is deliberately NO search box. No list endpoint supports `?search=`
 * (see src/lib/api/capabilities.ts) — DRF's SearchFilter is not configured
 * anywhere in the backend — so a search field would accept typing and change
 * nothing. Documents are the one exception and use their own dedicated
 * /documents/search/ endpoint.
 */

export interface FilterGroup {
  key: string;
  label: string;
  /** Currently applied value, or "" for none. */
  value: string;
  options: Array<{ value: string; label: string }>;
  allLabel?: string;
}

export interface FilterBarProps {
  groups: FilterGroup[];
  buildFilterHref: (key: string, value: string | null) => string;
  clearHref: string;
  activeFilterCount: number;
  /** Create, Export and other list-level actions. */
  actions?: React.ReactNode;
}

export function FilterBar({
  groups,
  buildFilterHref,
  clearHref,
  activeFilterCount,
  actions,
}: FilterBarProps) {
  const hasFilters = groups.length > 0;

  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-2" data-print="hide">
      {hasFilters
        ? groups.map((group) => (
            <div key={group.key} className="flex flex-wrap items-center gap-1.5">
              <span
                id={`filter-${group.key}`}
                className="text-2xs font-medium tracking-wide text-ink-500 uppercase"
              >
                {group.label}
              </span>
              <div
                role="group"
                aria-labelledby={`filter-${group.key}`}
                className="flex flex-wrap items-center gap-1"
              >
                <FilterChip
                  href={buildFilterHref(group.key, null)}
                  active={group.value === ""}
                  label={group.allLabel ?? "All"}
                />
                {group.options.map((option) => (
                  <FilterChip
                    key={option.value}
                    href={buildFilterHref(group.key, option.value)}
                    active={group.value === option.value}
                    label={option.label}
                  />
                ))}
              </div>
            </div>
          ))
        : null}

      {activeFilterCount > 0 ? (
        <Link
          href={appHref(clearHref)}
          className="text-xs font-medium text-brand-700 hover:underline"
        >
          Clear filters
          <span className="sr-only"> ({activeFilterCount} active)</span>
        </Link>
      ) : null}

      {actions ? <div className="ml-auto flex items-center gap-2">{actions}</div> : null}
    </div>
  );
}

function FilterChip({
  href,
  active,
  label,
}: {
  href: string;
  active: boolean;
  label: string;
}) {
  return (
    <Link
      href={appHref(href)}
      // aria-current marks the applied filter for a screen reader; the visual
      // fill alone carries nothing (spec §68).
      aria-current={active ? "true" : undefined}
      className={cn(
        "rounded-full px-2.5 py-1 text-xs font-medium transition-colors",
        active
          ? "bg-brand-700 text-white"
          : "border border-ink-200 bg-white text-ink-700 hover:bg-ink-50",
      )}
    >
      {label}
    </Link>
  );
}

/**
 * Explains a fixed server-side ordering, since the user cannot change it.
 * Saying so is better than leaving them to wonder why a column will not sort.
 */
export function SortNote({ description }: { description: string }) {
  return <>Sorted by {description}.</>;
}
