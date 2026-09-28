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
import { GSTR1_KEYS, gstr1Sections, unknownKeys } from "@/features/tax/gst";
import { GstSectionTable, TaxReportLinks, TaxScopeNotice, UnknownSectionsNotice } from "@/features/tax/gst-tables";
import type { Gstr1Summary } from "@/types/api/reports";

const PATH = "/tax/gstr-1";
const ENTRY = reportEntry(PATH);

export const metadata: Metadata = { title: ENTRY.title };

/**
 * The outward-supply figures a GSTR-1 is made of, per section of the API's
 * payload (b2b by customer GSTIN, b2c by place of supply, HSN by code and
 * rate…). Both dates are required, so a range is always sent.
 */
export default async function Gstr1Page({ searchParams }: { searchParams: Promise<RawSearchParams> }) {
  const session = await requireSession();
  const params = await searchParams;

  if (!roleHasPermission(session.role, PERMISSIONS.VIEW_RETURNS)) {
    return <ReportForbidden title={ENTRY.title} resource="GST returns" />;
  }

  const range = resolveMonthRange(params, session.timeZone);
  const result = await tryServer(() => serverApi.get<Gstr1Summary>("reports/tax/gstr1-summary", { query: range }));

  return (
    <ReportShell
      title={ENTRY.title}
      description={ENTRY.description}
      // Nested summary: the backend wires no CSV — null here.
      csvHref={csvExportHref("tax/gstr1-summary", range)}
      generatedAt={result.ok ? reportTimestamp() : null}
      timeZone={session.timeZone}
    >
      <TaxReportLinks current={PATH} query={hrefWith("", range)} />
      <TaxScopeNotice />
      <RangeControls path={PATH} range={range} presets={monthPresets(todayInZone(session.timeZone))} />
      <PeriodCaption
        from={result.ok ? result.data.period.from : range.from_date}
        to={result.ok ? result.data.period.to : range.to_date}
      />
      {isReversedRange(range) ? <ReversedRangeNotice /> : null}

      {!result.ok ? (
        <ReportError error={result.error} />
      ) : (
        <>
          <UnknownSectionsNotice keys={unknownKeys(result.data, GSTR1_KEYS)} />
          {gstr1Sections(result.data).map((section) => (
            <GstSectionTable key={section.path} section={section} />
          ))}
        </>
      )}
    </ReportShell>
  );
}
