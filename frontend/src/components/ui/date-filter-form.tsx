import { appHref } from "@/lib/routes";
import type * as React from "react";
import Link from "next/link";

/**
 * Date filters for reports and ledgers, as a plain GET form.
 *
 * A Server Component with no JavaScript: submitting navigates to the same
 * page with the dates in the query string, so a filtered report is a real,
 * shareable, bookmarkable URL (spec §66) and works before hydration. Other
 * active params are carried through as hidden inputs so changing the period
 * does not silently drop, say, the warehouse filter.
 */

export interface DateFilterField {
  name: string;
  label: string;
  value: string | undefined;
  required?: boolean;
}

export function DateFilterForm({
  action,
  fields,
  preserve = {},
  clearHref,
  submitLabel = "Apply",
  children,
}: {
  /** The page's own path. */
  action: string;
  fields: DateFilterField[];
  /** Other params to keep, e.g. `{ warehouse: "…" }`. Empty values are skipped. */
  preserve?: Record<string, string | undefined>;
  /** Shown when any field has a value. */
  clearHref?: string;
  submitLabel?: string;
  /** Extra controls rendered before the submit button (e.g. a select). */
  children?: React.ReactNode;
}) {
  const hasValue = fields.some((field) => Boolean(field.value));

  return (
    <form
      method="get"
      action={action}
      className="flex flex-wrap items-end gap-3"
      data-print="hide"
      aria-label="Report filters"
    >
      {Object.entries(preserve).map(([name, value]) =>
        value ? <input key={name} type="hidden" name={name} value={value} /> : null,
      )}

      {fields.map((field) => {
        const id = `filter-${field.name}`;
        return (
          <div key={field.name} className="flex flex-col gap-1">
            <label htmlFor={id} className="text-2xs font-medium tracking-wide text-ink-500 uppercase">
              {field.label}
            </label>
            <input
              id={id}
              type="date"
              name={field.name}
              defaultValue={field.value ?? ""}
              required={field.required}
              className="h-9 rounded-md border border-ink-300 bg-white px-2.5 text-sm text-ink-900 hover:border-ink-400 focus:border-brand-600"
            />
          </div>
        );
      })}

      {children}

      <button
        type="submit"
        className="inline-flex h-9 items-center rounded-md border border-ink-300 bg-white px-3.5 text-sm font-medium text-ink-800 transition-colors hover:bg-ink-50"
      >
        {submitLabel}
      </button>

      {clearHref && hasValue ? (
        <Link href={appHref(clearHref)} className="pb-2 text-xs font-medium text-brand-700 hover:underline">
          Reset
        </Link>
      ) : null}
    </form>
  );
}
