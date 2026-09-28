import type { Metadata } from "next";
import Link from "next/link";
import { Badge } from "@/components/ui/badge";
import { DateFilterForm } from "@/components/ui/date-filter-form";
import { EmptyState } from "@/components/ui/states";
import { serverApi, tryServer } from "@/lib/api/server";
import { csvExportHref } from "@/lib/api/capabilities";
import { errorCodeOf } from "@/lib/api/errors";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { todayInZone } from "@/lib/datetime";
import type { RawSearchParams } from "@/lib/list-query";
import { reportEntry } from "@/features/reports/catalogue";
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
import { cashFlowLines } from "@/features/reports/statement";
import { StatementTable } from "@/features/reports/statement-table";
import type { CashFlow } from "@/types/api/reports";

const PATH = "/reports/cash-flow";
const ENTRY = reportEntry(PATH);

export const metadata: Metadata = { title: ENTRY.title };

/**
 * Cash flow statement (indirect method) for a period.
 *
 * Which accounts are "cash" is never guessed: it is the GL account behind each
 * bank-kind bank account. With none configured the API answers 400
 * `cash_accounts_not_configured`, which is a setup step, not a failure — so it
 * gets an empty state pointing at bank accounts rather than an error.
 */
export default async function CashFlowPage({ searchParams }: { searchParams: Promise<RawSearchParams> }) {
  const session = await requireSession();
  const params = await searchParams;

  if (!roleHasPermission(session.role, PERMISSIONS.VIEW_REPORTS)) {
    return <ReportForbidden title={ENTRY.title} resource="the cash flow statement" />;
  }

  const fiscalStart = session.organization.fiscal_year_start_month;
  const today = todayInZone(session.timeZone);
  // from_date is REQUIRED by this endpoint (400 from_date_required); the
  // fiscal-year default guarantees one is always sent.
  const range = resolveReportRange(params, session.timeZone, fiscalStart);

  const result = await tryServer(() => serverApi.get<CashFlow>("reports/cash-flow", { query: range }));
  const generatedAt = result.ok ? reportTimestamp() : null;
  const notConfigured = !result.ok && errorCodeOf(result.error) === "cash_accounts_not_configured";
  const canManageBanking = roleHasPermission(session.role, PERMISSIONS.VIEW_BANK_ACCOUNTS);

  const glHref = (accountId: string) =>
    hrefWith("/accounting/general-ledger", { account: accountId, from_date: range.from_date, to_date: range.to_date });

  return (
    <ReportShell
      title={ENTRY.title}
      description={ENTRY.description}
      // Multi-section statement: the backend wires no CSV, so this is null.
      csvHref={csvExportHref("cash-flow", range)}
      generatedAt={generatedAt}
      timeZone={session.timeZone}
    >
      <div className="flex flex-col gap-3" data-print="hide">
        <PresetLinks links={rangePresetLinks(PATH, rangePresets(today, fiscalStart), range)} />
        <DateFilterForm
          action={PATH}
          fields={[
            { name: "from_date", label: "From", value: range.from_date, required: true },
            { name: "to_date", label: "To", value: range.to_date, required: true },
          ]}
          clearHref={PATH}
        />
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <PeriodCaption from={range.from_date} to={range.to_date} />
        {result.ok ? (
          result.data.reconciles ? (
            <Badge tone="success" marker>
              Reconciles to the change in cash
            </Badge>
          ) : (
            <Badge tone="danger" marker>
              Does not reconcile — the engine reports activities differ from the change in cash
            </Badge>
          )
        ) : null}
      </div>
      {isReversedRange(range) ? <ReversedRangeNotice /> : null}

      {notConfigured ? (
        <div className="rounded-lg border border-ink-200 bg-white">
          <EmptyState
            title="No bank account is set up as cash yet"
            description="The cash flow statement only counts accounts you have explicitly linked as bank accounts, so it cannot run until at least one bank account (not a credit card) exists."
            {...(canManageBanking ? { action: { label: "Go to bank accounts", href: "/banking/accounts" } } : {})}
          />
        </div>
      ) : !result.ok ? (
        <ReportError error={result.error} />
      ) : (
        <>
          <StatementTable caption="Cash flow statement" lines={cashFlowLines(result.data)} accountHref={glHref} />
          <p className="text-xs text-ink-500">
            Cash is the balance of{" "}
            {result.data.cash_accounts.map((account, index) => (
              <span key={account.bank_account_id}>
                {index > 0 ? ", " : null}
                <Link href={`/banking/accounts/${account.bank_account_id}`} className="text-brand-700 hover:underline">
                  {account.name}
                </Link>
              </span>
            ))}
            .
          </p>
        </>
      )}
    </ReportShell>
  );
}
