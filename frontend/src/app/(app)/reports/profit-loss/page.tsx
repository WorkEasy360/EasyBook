import { appHref } from "@/lib/routes";
import type { Metadata } from "next";
import Link from "next/link";
import { DateFilterForm } from "@/components/ui/date-filter-form";
import { Section } from "@/components/ui/detail";
import { serverApi, tryServer } from "@/lib/api/server";
import { csvExportHref } from "@/lib/api/capabilities";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { todayInZone } from "@/lib/datetime";
import { previousPeriod } from "@/lib/fiscal";
import { dateParamOf, type RawSearchParams } from "@/lib/list-query";
import { reportEntry } from "@/features/reports/catalogue";
import { ComparisonTable } from "@/features/reports/comparison-table";
import {
  hrefWith,
  isReversedRange,
  rangePresetLinks,
  rangePresets,
  reportTimestamp,
  resolveReportRange,
} from "@/features/reports/period";
import {
  PeriodCaption,
  PresetLinks,
  ReportError,
  ReportForbidden,
  ReportShell,
  ReversedRangeNotice,
} from "@/features/reports/report-shell";
import { pnlLines } from "@/features/reports/statement";
import { StatementTable } from "@/features/reports/statement-table";
import type { ProfitAndLoss } from "@/types/api/reports";

const PATH = "/reports/profit-loss";
const ENTRY = reportEntry(PATH);

export const metadata: Metadata = { title: ENTRY.title };

/**
 * Profit and loss for a period, from posted journal lines only. Sections and
 * totals are rendered exactly as the engine returns them; the optional
 * comparison is the API's own totals-level variance.
 */
export default async function ProfitLossPage({ searchParams }: { searchParams: Promise<RawSearchParams> }) {
  const session = await requireSession();
  const params = await searchParams;

  if (!roleHasPermission(session.role, PERMISSIONS.VIEW_REPORTS)) {
    return <ReportForbidden title={ENTRY.title} resource="the profit and loss report" />;
  }

  const fiscalStart = session.organization.fiscal_year_start_month;
  const today = todayInZone(session.timeZone);
  const range = resolveReportRange(params, session.timeZone, fiscalStart);
  const comparisonFrom = dateParamOf(params, "comparison_from");
  const comparisonTo = dateParamOf(params, "comparison_to");
  const comparing = Boolean(comparisonFrom || comparisonTo);

  const result = await tryServer(() =>
    serverApi.get<ProfitAndLoss>("reports/profit-loss", {
      query: { ...range, comparison_from: comparisonFrom, comparison_to: comparisonTo },
    }),
  );
  const generatedAt = result.ok ? reportTimestamp() : null;

  const comparison = { comparison_from: comparisonFrom, comparison_to: comparisonTo };
  const previous = previousPeriod({ ...range, label: "" });
  const glHref = (accountId: string) =>
    hrefWith("/accounting/general-ledger", { account: accountId, from_date: range.from_date, to_date: range.to_date });

  return (
    <ReportShell
      title={ENTRY.title}
      description={ENTRY.description}
      // Multi-section statement: the backend wires no CSV, so this is null.
      csvHref={csvExportHref("profit-loss", range)}
      generatedAt={generatedAt}
      timeZone={session.timeZone}
    >
      <div className="flex flex-col gap-3" data-print="hide">
        <PresetLinks links={rangePresetLinks(PATH, rangePresets(today, fiscalStart), range, comparison)} />
        <DateFilterForm
          action={PATH}
          fields={[
            { name: "from_date", label: "From", value: range.from_date, required: true },
            { name: "to_date", label: "To", value: range.to_date, required: true },
            { name: "comparison_from", label: "Compare from", value: comparisonFrom },
            { name: "comparison_to", label: "Compare to", value: comparisonTo },
          ]}
          clearHref={PATH}
        />
        <p className="text-xs text-ink-500">
          {comparing ? (
            <Link href={appHref(hrefWith(PATH, range))} className="font-medium text-brand-700 hover:underline">
              Remove comparison
            </Link>
          ) : (
            <Link
              href={appHref(hrefWith(PATH, {
                ...range,
                comparison_from: previous.from_date,
                comparison_to: previous.to_date,
              }))}
              className="font-medium text-brand-700 hover:underline"
            >
              Compare with the previous period of the same length
            </Link>
          )}
        </p>
      </div>

      <PeriodCaption from={result.ok ? result.data.period.from_date : range.from_date} to={range.to_date} />
      {isReversedRange(range) ? <ReversedRangeNotice /> : null}

      {!result.ok ? (
        <ReportError error={result.error} />
      ) : (
        <>
          <StatementTable caption="Profit and loss" lines={pnlLines(result.data)} accountHref={glHref} />
          {result.data.variance && result.data.comparison_period ? (
            <Section
              title="Comparison"
              description="Totals only — the engine compares statement totals, not individual accounts."
            >
              <ComparisonTable
                variance={result.data.variance}
                period={result.data.period}
                comparisonPeriod={result.data.comparison_period}
              />
            </Section>
          ) : null}
        </>
      )}
    </ReportShell>
  );
}
