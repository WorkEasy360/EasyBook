import { appHref } from "@/lib/routes";
import type * as React from "react";
import Link from "next/link";
import { Section } from "@/components/ui/detail";
import { Money } from "@/components/ui/money";
import { formatPercent } from "@/lib/money";
import type { GstSection } from "./gst";

/**
 * One GST section as a table. Amount columns are the bucket's own fields; the
 * document count column appears only when the API's bucket carries one. No
 * totals row — the API returns none for these maps.
 */
export function GstSectionTable({ section, pathPrefix }: { section: GstSection; pathPrefix?: string }) {
  // Nested payloads (the GST summary embeds gstr1/gstr3b) show their full path.
  const qualify = (path: string) => (pathPrefix ? `${pathPrefix}.${path}` : path);
  const showCount = section.rows.some((row) => "document_count" in row.bucket);
  const columnCount = 6 + (section.kind === "hsn" ? 1 : 0) + (showCount ? 1 : 0);

  return (
    <Section title={section.title}>
      <div className="overflow-x-auto rounded-lg border border-ink-200 bg-white">
        <table className="w-full min-w-max border-collapse text-sm">
          <caption className="sr-only">
            {section.title} ({qualify(section.path)})
          </caption>
          <thead>
            <tr className="border-b border-ink-200 bg-ink-50 text-2xs font-semibold tracking-wide text-ink-600 uppercase">
              <th scope="col" className="px-3 py-2 text-left">
                {section.keyLabel}
              </th>
              {section.kind === "hsn" ? (
                <th scope="col" className="px-3 py-2 text-right">
                  Rate
                </th>
              ) : null}
              <th scope="col" className="px-3 py-2 text-right">
                Taxable value
              </th>
              <th scope="col" className="px-3 py-2 text-right">
                CGST
              </th>
              <th scope="col" className="px-3 py-2 text-right">
                SGST
              </th>
              <th scope="col" className="px-3 py-2 text-right">
                IGST
              </th>
              <th scope="col" className="px-3 py-2 text-right">
                Cess
              </th>
              {showCount ? (
                <th scope="col" className="px-3 py-2 text-right">
                  Documents
                </th>
              ) : null}
            </tr>
          </thead>
          <tbody className="divide-y divide-ink-100">
            {section.rows.length === 0 ? (
              <tr>
                <td colSpan={columnCount} className="px-3 py-3 text-sm text-ink-500 italic">
                  No entries for this period.
                </td>
              </tr>
            ) : (
              section.rows.map((row) => (
                <tr key={row.key}>
                  <th scope="row" className="px-3 py-2 text-left font-normal">
                    <span className="text-ink-900">{row.label}</span>
                    {row.field ? <code className="ml-2 text-2xs text-ink-500">{qualify(row.field)}</code> : null}
                  </th>
                  {section.kind === "hsn" ? (
                    <td className="numeric px-3 py-2 text-right">
                      {row.rate ? formatPercent(row.rate) : <span className="text-ink-400">—</span>}
                    </td>
                  ) : null}
                  <td className="numeric px-3 py-2 text-right">
                    <Money value={row.bucket.taxable_value} />
                  </td>
                  <td className="numeric px-3 py-2 text-right">
                    <Money value={row.bucket.cgst} />
                  </td>
                  <td className="numeric px-3 py-2 text-right">
                    <Money value={row.bucket.sgst} />
                  </td>
                  <td className="numeric px-3 py-2 text-right">
                    <Money value={row.bucket.igst} />
                  </td>
                  <td className="numeric px-3 py-2 text-right">
                    <Money value={row.bucket.cess} />
                  </td>
                  {showCount ? (
                    <td className="numeric px-3 py-2 text-right tabular">
                      {"document_count" in row.bucket ? row.bucket.document_count : "—"}
                    </td>
                  ) : null}
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
      <p className="text-2xs text-ink-500">
        API field: <code>{qualify(section.path)}</code>
      </p>
    </Section>
  );
}

/**
 * What these screens are NOT. The compliance module has no REST API, so
 * nothing here generates an IRN or e-Way Bill or files a return — see the
 * blocked entries in src/lib/navigation.ts.
 */
export function TaxScopeNotice({ children }: { children?: React.ReactNode }) {
  return (
    <div role="note" className="rounded-md border border-info-100 bg-info-50 px-3 py-2.5 text-sm text-info-700">
      <p>
        These are figures computed from posted documents, for review. e-Invoice (IRN) generation, e-Way Bill
        generation and GST return filing are not available in EasyBook.
      </p>
      {children}
    </div>
  );
}

/** Announces payload sections this build does not render yet, rather than hiding them. */
export function UnknownSectionsNotice({ keys }: { keys: string[] }) {
  if (keys.length === 0) return null;
  return (
    <p role="status" className="rounded-md border border-warning-100 bg-warning-50 px-3 py-2 text-sm text-warning-700">
      The report returned sections this screen does not display yet: {keys.join(", ")}.
    </p>
  );
}

/** Links between the tax report pages. */
export function TaxReportLinks({ current, query }: { current: string; query: string }) {
  const links = [
    { href: "/tax/summary", label: "GST summary" },
    { href: "/tax/gstr-1", label: "GSTR-1 summary" },
    { href: "/tax/gstr-3b", label: "GSTR-3B summary" },
    { href: "/tax/registers", label: "Tax registers" },
  ];
  return (
    <nav aria-label="Tax reports" data-print="hide" className="flex flex-wrap gap-x-4 gap-y-1 text-xs">
      {links.map((link) =>
        link.href === current ? (
          <span key={link.href} aria-current="page" className="font-semibold text-ink-900">
            {link.label}
          </span>
        ) : (
          <Link key={link.href} href={appHref(`${link.href}${query}`)} className="font-medium text-brand-700 hover:underline">
            {link.label}
          </Link>
        ),
      )}
    </nav>
  );
}
