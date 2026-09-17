import type { Metadata } from "next";
import { Badge } from "@/components/ui/badge";
import { DateFilterForm } from "@/components/ui/date-filter-form";
import { serverApi, tryServer } from "@/lib/api/server";
import { csvExportHref } from "@/lib/api/capabilities";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { todayInZone } from "@/lib/datetime";
import { dateParamOf, type RawSearchParams } from "@/lib/list-query";
import { reportEntry } from "@/features/reports/catalogue";
import { asOfPresetLinks, asOfPresets, hrefWith, reportTimestamp } from "@/features/reports/period";
import {
  PeriodCaption,
  PresetLinks,
  ReportError,
  ReportForbidden,
  ReportShell,
} from "@/features/reports/report-shell";
import { balanceSheetLines } from "@/features/reports/statement";
import { StatementTable } from "@/features/reports/statement-table";
import type { BalanceSheet } from "@/types/api/reports";

const PATH = "/reports/balance-sheet";
const ENTRY = reportEntry(PATH);

export const metadata: Metadata = { title: ENTRY.title };

/**
 * Balance sheet as of a date. "Current Period Earnings" is the engine's
 * computed cumulative net profit (no year-end closing journal is ever
 * posted), so it has no account to drill into. The balance check is the
 * engine's `is_balanced` — the UI never compares the totals itself.
 */
export default async function BalanceSheetPage({ searchParams }: { searchParams: Promise<RawSearchParams> }) {
  const session = await requireSession();
  const params = await searchParams;

  if (!roleHasPermission(session.role, PERMISSIONS.VIEW_REPORTS)) {
    return <ReportForbidden title={ENTRY.title} resource="the balance sheet" />;
  }

  const today = todayInZone(session.timeZone);
  const asOf = dateParamOf(params, "as_of_date") ?? today;
  const fiscalStart = session.organization.fiscal_year_start_month;

  const result = await tryServer(() =>
    serverApi.get<BalanceSheet>("reports/balance-sheet", { query: { as_of_date: asOf } }),
  );
  const generatedAt = result.ok ? reportTimestamp() : null;

  // A balance is cumulative from inception, so the ledger drill-down runs up
  // to the as-of date with no start date.
  const glHref = (accountId: string) =>
    hrefWith("/accounting/general-ledger", { account: accountId, to_date: asOf });

  return (
    <ReportShell
      title={ENTRY.title}
      description={ENTRY.description}
      // Multi-section statement: the backend wires no CSV, so this is null.
      csvHref={csvExportHref("balance-sheet", { as_of_date: asOf })}
      generatedAt={generatedAt}
      timeZone={session.timeZone}
    >
      <div className="flex flex-col gap-3" data-print="hide">
        <PresetLinks links={asOfPresetLinks(PATH, asOfPresets(today, fiscalStart), asOf)} label="Quick dates" />
        <DateFilterForm
          action={PATH}
          fields={[{ name: "as_of_date", label: "As of", value: asOf, required: true }]}
          clearHref={PATH}
        />
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <PeriodCaption asOf={result.ok ? result.data.as_of_date : asOf} />
        {result.ok ? (
          result.data.is_balanced ? (
            <Badge tone="success" marker>
              Balanced: assets equal liabilities plus equity
            </Badge>
          ) : (
            <Badge tone="danger" marker>
              Not balanced — the engine reports assets do not equal liabilities plus equity
            </Badge>
          )
        ) : null}
      </div>

      {!result.ok ? (
        <ReportError error={result.error} />
      ) : (
        <StatementTable caption="Balance sheet" lines={balanceSheetLines(result.data)} accountHref={glHref} />
      )}
    </ReportShell>
  );
}
