import { appHref } from "@/lib/routes";
import type * as React from "react";
import Link from "next/link";
import { PageHeader } from "@/components/ui/page-header";
import { PageBody } from "@/components/ui/detail";
import { DateFilterForm } from "@/components/ui/date-filter-form";
import { LinkButton } from "@/components/ui/link-button";
import { PrintButton } from "@/components/ui/print-button";
import { ErrorState, ForbiddenState } from "@/components/ui/states";
import { Icons } from "@/components/ui/icons";
import { ApiError, referenceOf } from "@/lib/api/errors";
import { cn } from "@/lib/cn";
import { formatDate, formatDateTime } from "@/lib/datetime";
import { asOfPresetLinks, rangePresetLinks, type PresetLink } from "./period";

/**
 * Shared frame for every report page: the header with export and print, the
 * body, and the provenance note. One component so every report states where
 * its figures came from, and none can grow an export button on its own.
 */

export function ReportShell({
  title,
  description,
  csvHref,
  actions,
  generatedAt,
  timeZone,
  children,
}: {
  title: string;
  description?: React.ReactNode;
  /**
   * From csvExportHref() ONLY — it returns null for a report the backend has
   * no CSV for, and then no button is rendered (spec §87).
   */
  csvHref?: string | null;
  actions?: React.ReactNode;
  /** When the engine was asked. Omitted while the report failed to load. */
  generatedAt?: string | null;
  timeZone: string;
  children: React.ReactNode;
}) {
  return (
    <>
      <PageHeader
        title={title}
        breadcrumbs={[{ label: "Reports", href: "/reports" }, { label: title }]}
        {...(description ? { description } : {})}
        actions={
          <>
            {actions}
            {csvHref ? (
              <LinkButton href={csvHref} download>
                <Icons.download className="size-3.5" aria-hidden="true" />
                Export CSV
              </LinkButton>
            ) : null}
            <PrintButton />
          </>
        }
      />
      <PageBody>
        {children}
        {generatedAt ? <EngineNote generatedAt={generatedAt} timeZone={timeZone} /> : null}
      </PageBody>
    </>
  );
}

export function EngineNote({ generatedAt, timeZone }: { generatedAt: string; timeZone: string }) {
  return (
    <p className="text-xs text-ink-500">
      Figures from the accounting engine as of {formatDateTime(generatedAt, { timeZone })}.
    </p>
  );
}

/** The whole page for a role without the report's permission. */
export function ReportForbidden({ title, resource }: { title: string; resource: string }) {
  return (
    <>
      <PageHeader title={title} breadcrumbs={[{ label: "Reports", href: "/reports" }, { label: title }]} />
      <ForbiddenState resource={resource} />
    </>
  );
}

/** A report request that failed, with the request id support needs. */
export function ReportError({ error }: { error: ApiError | Error }) {
  const forbidden = error instanceof ApiError && error.isForbidden;
  return (
    <div className="rounded-lg border border-ink-200 bg-white">
      <ErrorState
        title={forbidden ? "You do not have access to this report" : "Could not load this report"}
        message={error.message}
        reference={referenceOf(error)}
      />
    </div>
  );
}

/** Preset period links, marked with aria-current when the page shows that period. */
export function PresetLinks({ links, label = "Quick periods" }: { links: PresetLink[]; label?: string }) {
  return (
    <nav aria-label={label} data-print="hide">
      <ul className="flex flex-wrap items-center gap-1.5">
        {links.map((link) => (
          <li key={link.href}>
            <Link
              href={appHref(link.href)}
              aria-current={link.active ? "true" : undefined}
              className={cn(
                "inline-block rounded-full px-2.5 py-1 text-xs font-medium transition-colors",
                link.active
                  ? "bg-brand-700 text-white"
                  : "border border-ink-200 bg-white text-ink-700 hover:bg-ink-50",
              )}
            >
              {link.label}
            </Link>
          </li>
        ))}
      </ul>
    </nav>
  );
}

/**
 * The period in words. The filter form is hidden when printing, so this is
 * what tells a printed report which dates it covers.
 */
export function PeriodCaption({
  from,
  to,
  asOf,
  emptyText = "All dates",
}: {
  from?: string | null;
  to?: string | null;
  asOf?: string | null;
  /** What to say when no date applies, e.g. "Current position". */
  emptyText?: string;
}) {
  let text: string;
  if (asOf) text = `As of ${formatDate(asOf)}`;
  else if (from && to) text = `${formatDate(from)} – ${formatDate(to)}`;
  else if (to) text = `Up to ${formatDate(to)}`;
  else if (from) text = `From ${formatDate(from)}`;
  else text = emptyText;
  return <p className="text-sm font-medium text-ink-800">{text}</p>;
}

/** Shown when from > to: the API accepts it and simply returns nothing. */
export function ReversedRangeNotice() {
  return (
    <p role="status" className="rounded-md border border-warning-100 bg-warning-50 px-3 py-2 text-sm text-warning-700">
      The start date is after the end date, so this period contains no transactions.
    </p>
  );
}

/** A native select for a report's GET filter form. */
export function FilterSelect({
  name,
  label,
  value,
  options,
  allLabel = "All",
}: {
  name: string;
  label: string;
  value: string | undefined;
  options: Array<{ value: string; label: string }>;
  allLabel?: string;
}) {
  const id = `filter-${name}`;
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className="text-2xs font-medium tracking-wide text-ink-500 uppercase">
        {label}
      </label>
      <select
        id={id}
        name={name}
        defaultValue={value ?? ""}
        className="h-9 max-w-64 rounded-md border border-ink-300 bg-white px-2 pr-8 text-sm text-ink-900 hover:border-ink-400 focus:border-brand-600"
      >
        <option value="">{allLabel}</option>
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </div>
  );
}

/** A text input for a report's GET filter form. */
export function FilterText({ name, label, value }: { name: string; label: string; value: string | undefined }) {
  const id = `filter-${name}`;
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className="text-2xs font-medium tracking-wide text-ink-500 uppercase">
        {label}
      </label>
      <input
        id={id}
        type="text"
        name={name}
        defaultValue={value ?? ""}
        className="h-9 w-40 rounded-md border border-ink-300 bg-white px-2.5 text-sm text-ink-900 hover:border-ink-400 focus:border-brand-600"
      />
    </div>
  );
}

/**
 * Preset links plus the GET date form for a from/to report. Other active
 * filters are carried through both, so changing the period never drops them.
 */
export function RangeControls({
  path,
  range,
  presets,
  preserve = {},
  formPreserve,
  children,
}: {
  path: string;
  range: { from_date: string; to_date: string };
  presets: Array<{ label: string; from_date: string; to_date: string }>;
  /** Every other active filter — kept on the preset links. */
  preserve?: Record<string, string | undefined>;
  /**
   * Hidden inputs for the form. Defaults to `preserve` when there are no
   * extra controls, and to nothing when there are (the controls carry their
   * own values). Pass explicitly for a filter that has no visible control.
   */
  formPreserve?: Record<string, string | undefined>;
  /** Extra filter controls (FilterSelect/FilterText) inside the same form. */
  children?: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-3" data-print="hide">
      <PresetLinks links={rangePresetLinks(path, presets, range, preserve)} />
      <DateFilterForm
        action={path}
        fields={[
          { name: "from_date", label: "From", value: range.from_date, required: true },
          { name: "to_date", label: "To", value: range.to_date, required: true },
        ]}
        preserve={formPreserve ?? (children ? {} : preserve)}
        clearHref={path}
      >
        {children}
      </DateFilterForm>
    </div>
  );
}

/** Preset links plus the GET form for an as-of report. */
export function AsOfControls({
  path,
  asOf,
  presets,
  preserve = {},
  formPreserve,
  required = false,
  children,
}: {
  path: string;
  asOf: string | undefined;
  presets: Array<{ label: string; date: string }>;
  preserve?: Record<string, string | undefined>;
  /** As on RangeControls. */
  formPreserve?: Record<string, string | undefined>;
  required?: boolean;
  children?: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-3" data-print="hide">
      <PresetLinks links={asOfPresetLinks(path, presets, asOf, preserve)} label="Quick dates" />
      <DateFilterForm
        action={path}
        fields={[{ name: "as_of_date", label: "As of", value: asOf, required }]}
        preserve={formPreserve ?? (children ? {} : preserve)}
        clearHref={path}
      >
        {children}
      </DateFilterForm>
    </div>
  );
}
