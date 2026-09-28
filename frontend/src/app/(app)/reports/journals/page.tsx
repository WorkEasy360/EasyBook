import { appHref } from "@/lib/routes";
import type { Metadata } from "next";
import Link from "next/link";
import { DataTable, type Column } from "@/components/ui/data-table";
import { Money } from "@/components/ui/money";
import { JOURNAL_STATUS, StatusBadge, statusOptions } from "@/components/ui/status-badge";
import { serverApi, tryServer } from "@/lib/api/server";
import { csvExportHref } from "@/lib/api/capabilities";
import type { ApiError } from "@/lib/api/errors";
import { recordsById } from "@/lib/api/lookups";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { formatDate, todayInZone } from "@/lib/datetime";
import { isZero } from "@/lib/money";
import { paramOf, parseListQuery, type RawSearchParams } from "@/lib/list-query";
import { reportEntry } from "@/features/reports/catalogue";
import {
  isReversedRange,
  pagedHrefBuilder,
  rangePresets,
  reportTimestamp,
  resolveReportRange,
  uuidParamOf,
} from "@/features/reports/period";
import {
  FilterSelect,
  FilterText,
  PeriodCaption,
  RangeControls,
  ReportForbidden,
  ReportShell,
  ReversedRangeNotice,
} from "@/features/reports/report-shell";
import { resolveSource } from "@/features/reports/source-routes";
import type { Account, JournalEntry } from "@/types/api/accounting";
import { JOURNAL_SOURCE_TYPES } from "@/types/api/reports";

const PATH = "/reports/journals";
const ENTRY = reportEntry(PATH);

export const metadata: Metadata = { title: ENTRY.title };

/**
 * Journals over a period, filterable by account, number, source and status —
 * paginated by the API. With no status the endpoint returns POSTED journals
 * only, so the "all" option says so. Lines are shown as the engine stored
 * them; no journal total is computed here.
 */
export default async function JournalReportPage({ searchParams }: { searchParams: Promise<RawSearchParams> }) {
  const session = await requireSession();
  const params = await searchParams;

  // reports/api/views.py :: JournalReportView.required_permission
  if (!roleHasPermission(session.role, PERMISSIONS.VIEW_TRANSACTIONS)) {
    return <ReportForbidden title={ENTRY.title} resource="the journal report" />;
  }

  const fiscalStart = session.organization.fiscal_year_start_month;
  const range = resolveReportRange(params, session.timeZone, fiscalStart);
  const accountId = uuidParamOf(params, "account");
  const journalNumber = paramOf(params, "journal_number")?.trim() || undefined;
  const rawSource = paramOf(params, "source_type");
  const sourceType = rawSource && (JOURNAL_SOURCE_TYPES as readonly string[]).includes(rawSource) ? rawSource : undefined;
  const rawStatus = paramOf(params, "status");
  const status = rawStatus && rawStatus in JOURNAL_STATUS ? rawStatus : undefined;
  const { page, pageSize, apiParams } = parseListQuery(PATH, "reports/journals", params);
  const filters = { ...range, account: accountId, journal_number: journalNumber, source_type: sourceType, status };

  const canViewAccounts = roleHasPermission(session.role, PERMISSIONS.VIEW_ACCOUNTING);
  const [result, accountPage] = await Promise.all([
    tryServer(() =>
      serverApi.list<JournalEntry>("reports/journals", {
        query: { ...filters, page: apiParams.page, page_size: apiParams.page_size },
      }),
    ),
    canViewAccounts
      ? tryServer(() => serverApi.list<Account>("accounting/accounts", { query: { page_size: 200 } }))
      : Promise.resolve(null),
  ]);

  // Lines carry account ids only. One page of the chart (which also feeds the
  // account filter) plus a backfill for any id not on it names every line.
  const accounts = new Map<string, Account>(
    accountPage?.ok ? accountPage.data.results.map((account) => [account.id, account]) : [],
  );
  const lineAccountIds = result.ok ? result.data.results.flatMap((entry) => entry.lines.map((line) => line.account)) : [];
  const missing = [...lineAccountIds, accountId].filter((id): id is string => Boolean(id) && !accounts.has(id as string));
  if (canViewAccounts && missing.length > 0) {
    for (const [id, account] of await recordsById<Account>("accounting/accounts", missing)) accounts.set(id, account);
  }
  const accountOptions = [...accounts.values()]
    .sort((a, b) => a.code.localeCompare(b.code))
    .map((account) => ({ value: account.id, label: `${account.code} · ${account.name}` }));

  const columns: Column<JournalEntry>[] = [
    {
      key: "journal_number",
      header: "Journal",
      cell: (row) => <span className="tabular">{row.journal_number || "Draft"}</span>,
    },
    {
      key: "posting_date",
      header: "Date",
      cell: (row) => <span className="tabular whitespace-nowrap">{formatDate(row.posting_date)}</span>,
    },
    {
      key: "memo",
      header: "Memo",
      // The lines column is wide; memo yields first so status stays in view.
      hideBelow: "lg",
      cell: (row) => <span className="text-ink-700">{row.memo || row.reference || "—"}</span>,
    },
    {
      key: "source",
      header: "Source",
      hideBelow: "sm",
      cell: (row) => {
        const source = resolveSource(row.source_type, row.source_id);
        return source.href ? (
          <Link href={appHref(source.href)} className="text-brand-700 hover:underline">
            {source.label}
          </Link>
        ) : (
          <span className="text-ink-600">{source.label}</span>
        );
      },
    },
    {
      key: "lines",
      header: "Lines",
      cell: (row) => (
        <ul className="flex min-w-64 flex-col gap-0.5 text-xs">
          {row.lines.map((line) => {
            const account = accounts.get(line.account);
            // A line is a debit or a credit; the other side is zero.
            const isDebit = !isZero(line.debit);
            return (
              <li key={line.id} className="flex items-baseline justify-between gap-3">
                <span className={isDebit ? "text-ink-800" : "pl-4 text-ink-700"}>
                  {account ? `${account.code} · ${account.name}` : "Account"}
                </span>
                <span className="whitespace-nowrap">
                  <span className="mr-1 text-2xs text-ink-500">{isDebit ? "Dr" : "Cr"}</span>
                  <Money value={isDebit ? line.debit : line.credit} />
                </span>
              </li>
            );
          })}
        </ul>
      ),
    },
    {
      key: "status",
      header: "Status",
      cell: (row) => <StatusBadge status={row.status} map={JOURNAL_STATUS} size="sm" />,
    },
  ];

  return (
    <ReportShell
      title={ENTRY.title}
      description={ENTRY.description}
      // Paginated transaction detail: the backend wires no CSV — null here.
      csvHref={csvExportHref("journals", filters)}
      generatedAt={result.ok ? reportTimestamp() : null}
      timeZone={session.timeZone}
    >
      <RangeControls
        path={PATH}
        range={range}
        presets={rangePresets(todayInZone(session.timeZone), fiscalStart)}
        preserve={{ account: accountId, journal_number: journalNumber, source_type: sourceType, status }}
        // Without chart-of-accounts access there is no account select, so an
        // account filter from the URL rides along as a hidden input instead.
        formPreserve={accountOptions.length > 0 ? {} : { account: accountId }}
      >
        {accountOptions.length > 0 ? (
          <FilterSelect name="account" label="Account" value={accountId} allLabel="All accounts" options={accountOptions} />
        ) : null}
        <FilterText name="journal_number" label="Journal number" value={journalNumber} />
        <FilterSelect
          name="source_type"
          label="Source"
          value={sourceType}
          allLabel="Any source"
          options={JOURNAL_SOURCE_TYPES.map((value) => ({ value, label: resolveSource(value, null).label }))}
        />
        <FilterSelect
          name="status"
          label="Status"
          value={status}
          allLabel="Posted (default)"
          options={statusOptions(JOURNAL_STATUS)}
        />
      </RangeControls>
      <PeriodCaption from={range.from_date} to={range.to_date} />
      {isReversedRange(range) ? <ReversedRangeNotice /> : null}

      <DataTable
        caption="Journals"
        columns={columns}
        data={result.ok ? result.data : undefined}
        error={result.ok ? null : (result.error as ApiError)}
        getRowId={(row) => row.id}
        getRowHref={(row) => `/accounting/journals/${row.id}`}
        emptyTitle="No journals match"
        emptyDescription="Journals are written when documents are posted, or entered manually by an accountant."
        page={page}
        pageSize={pageSize}
        buildPageHref={pagedHrefBuilder(PATH, filters, pageSize)}
        note="Sorted by posting date, then journal number."
      />
    </ReportShell>
  );
}
