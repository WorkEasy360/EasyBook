import type { Metadata } from "next";
import { serverApi, tryServer } from "@/lib/api/server";
import { csvExportHref } from "@/lib/api/capabilities";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { todayInZone } from "@/lib/datetime";
import type { RawSearchParams } from "@/lib/list-query";
import { reportEntry } from "@/features/reports/catalogue";
import { hrefWith, isReversedRange, monthPresets, reportTimestamp, resolveMonthRange } from "@/features/reports/period";
import {
  PeriodCaption,
  RangeControls,
  ReportError,
  ReportForbidden,
  ReportShell,
  ReversedRangeNotice,
} from "@/features/reports/report-shell";
import {
  GST_SUMMARY_KEYS,
  GSTR1_KEYS,
  GSTR3B_KEYS,
  gstTaxTotalsSection,
  gstr1Sections,
  gstr3bSections,
  unknownKeys,
} from "@/features/tax/gst";
import { GstSectionTable, TaxReportLinks, TaxScopeNotice, UnknownSectionsNotice } from "@/features/tax/gst-tables";
import type { GstSummary } from "@/types/api/reports";

const PATH = "/tax/summary";
const ENTRY = reportEntry(PATH);

export const metadata: Metadata = { title: ENTRY.title };

/**
 * The combined GST view for a period: the output and input register totals
 * (as the API totals them), then the embedded GSTR-1 and GSTR-3B summaries,
 * every section rendered.
 */
export default async function GstSummaryPage({ searchParams }: { searchParams: Promise<RawSearchParams> }) {
  const session = await requireSession();
  const params = await searchParams;

  if (!roleHasPermission(session.role, PERMISSIONS.VIEW_RETURNS)) {
    return <ReportForbidden title={ENTRY.title} resource="GST returns" />;
  }

  const range = resolveMonthRange(params, session.timeZone);
  const result = await tryServer(() => serverApi.get<GstSummary>("reports/tax/gst-summary", { query: range }));

  return (
    <ReportShell
      title={ENTRY.title}
      description={ENTRY.description}
      // Nested summary: the backend wires no CSV — null here.
      csvHref={csvExportHref("tax/gst-summary", range)}
      generatedAt={result.ok ? reportTimestamp() : null}
      timeZone={session.timeZone}
    >
      <TaxReportLinks current={PATH} query={hrefWith("", range)} />
      <TaxScopeNotice />
      <RangeControls path={PATH} range={range} presets={monthPresets(todayInZone(session.timeZone))} />
      <PeriodCaption
        from={result.ok ? result.data.period.from_date : range.from_date}
        to={result.ok ? result.data.period.to_date : range.to_date}
      />
      {isReversedRange(range) ? <ReversedRangeNotice /> : null}

      {!result.ok ? (
        <ReportError error={result.error} />
      ) : (
        <>
          <UnknownSectionsNotice
            keys={[
              ...unknownKeys(result.data, GST_SUMMARY_KEYS),
              ...unknownKeys(result.data.gstr1, GSTR1_KEYS).map((key) => `gstr1.${key}`),
              ...unknownKeys(result.data.gstr3b, GSTR3B_KEYS).map((key) => `gstr3b.${key}`),
            ]}
          />
          <GstSectionTable section={gstTaxTotalsSection(result.data)} />

          <h2 className="border-t border-ink-200 pt-4 text-base font-semibold text-ink-900">
            GSTR-1 figures <code className="ml-1 text-xs font-normal text-ink-500">gstr1</code>
          </h2>
          {gstr1Sections(result.data.gstr1).map((section) => (
            <GstSectionTable key={`gstr1.${section.path}`} section={section} pathPrefix="gstr1" />
          ))}

          <h2 className="border-t border-ink-200 pt-4 text-base font-semibold text-ink-900">
            GSTR-3B figures <code className="ml-1 text-xs font-normal text-ink-500">gstr3b</code>
          </h2>
          {gstr3bSections(result.data.gstr3b).map((section) => (
            <GstSectionTable key={`gstr3b.${section.path}`} section={section} pathPrefix="gstr3b" />
          ))}
        </>
      )}
    </ReportShell>
  );
}
