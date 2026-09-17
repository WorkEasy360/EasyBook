import { appHref } from "@/lib/routes";
import type { Metadata } from "next";
import Link from "next/link";
import { PageHeader } from "@/components/ui/page-header";
import { PageBody, Section } from "@/components/ui/detail";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { StatCard, StatGrid } from "@/components/ui/stat-card";
import { Money, Quantity } from "@/components/ui/money";
import { Badge } from "@/components/ui/badge";
import { EmptyState, ForbiddenState } from "@/components/ui/states";
import { Icons } from "@/components/ui/icons";
import { serverApi, tryServer } from "@/lib/api/server";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission, type Permission } from "@/lib/authz/permissions";
import { currentFiscalYear } from "@/lib/fiscal";
import { todayInZone, formatDate, daysBetween } from "@/lib/datetime";
import { money } from "@/lib/money";
import {
  AGEING_BUCKETS,
  AGEING_BUCKET_LABELS,
  type AgeingReport,
  type BillReportRow,
  type InvoiceReportRow,
  type PayablesAgeing,
  type ProfitAndLoss,
  type ReceivablesAgeing,
  type RowsEnvelope,
} from "@/types/api/reports";
import type { LowStockItemRow } from "@/types/api/inventory";

export const metadata: Metadata = { title: "Dashboard" };

/**
 * The dashboard.
 *
 * Every figure comes from a report endpoint — the deterministic engine's
 * output, displayed unchanged (spec §17). Nothing here is derived from
 * documents the browser happens to hold.
 *
 * There is no single dashboard endpoint, so this fans out across several
 * reports IN PARALLEL. Each panel degrades on its own: a slow or forbidden
 * report shows an inline message rather than blanking the page (spec §84).
 */
export default async function DashboardPage() {
  const session = await requireSession();

  if (!roleHasPermission(session.role, PERMISSIONS.VIEW_REPORTS)) {
    return (
      <>
        <PageHeader title="Dashboard" />
        <ForbiddenState resource="financial reports" />
      </>
    );
  }

  const today = todayInZone(session.timeZone);
  const fiscal = currentFiscalYear(today, session.organization.fiscal_year_start_month);
  const can = (permission: Permission) => roleHasPermission(session.role, permission);

  const [pnl, receivables, payables, overdueInvoices, overdueBills, lowStock] = await Promise.all([
    tryServer(() =>
      serverApi.get<ProfitAndLoss>("reports/profit-loss", {
        query: { from_date: fiscal.from_date, to_date: today },
      }),
    ),
    tryServer(() =>
      serverApi.get<ReceivablesAgeing>("reports/receivables/ageing", { query: { as_of_date: today } }),
    ),
    tryServer(() =>
      serverApi.get<PayablesAgeing>("reports/payables/ageing", { query: { as_of_date: today } }),
    ),
    // Both overdue reports return a `{rows: [...]}` envelope (verified live),
    // with document-specific keys: invoice_id/invoice_number/customer_name vs
    // bill_id/bill_number/vendor_name.
    tryServer(() =>
      serverApi.get<RowsEnvelope<InvoiceReportRow>>("reports/receivables/overdue-invoices", {
        query: { as_of_date: today },
      }),
    ),
    tryServer(() =>
      serverApi.get<RowsEnvelope<BillReportRow>>("reports/payables/overdue-bills", {
        query: { as_of_date: today },
      }),
    ),
    can(PERMISSIONS.VIEW_INVENTORY)
      ? tryServer(() => serverApi.get<{ rows: LowStockItemRow[] }>("reports/inventory/low-stock"))
      : Promise.resolve({ ok: false as const, error: new Error("no permission") }),
  ]);

  const totals = pnl.ok ? pnl.data.totals : null;
  // `grand_total`, not a `total` bucket — the ageing payload keeps the
  // overall figure outside the per-bucket totals.
  const receivableTotal = receivables.ok ? receivables.data.grand_total : null;
  const payableTotal = payables.ok ? payables.data.grand_total : null;

  const overdueInvoiceRows: OverdueRow[] = overdueInvoices.ok
    ? overdueInvoices.data.rows.map((row) => ({
        id: row.invoice_id,
        number: row.invoice_number,
        party: row.customer_name,
        dueDate: row.due_date,
        amountDue: row.amount_due,
        href: `/sales/invoices/${row.invoice_id}`,
      }))
    : [];
  const overdueBillRows: OverdueRow[] = overdueBills.ok
    ? overdueBills.data.rows.map((row) => ({
        id: row.bill_id,
        number: row.bill_number,
        party: row.vendor_name,
        dueDate: row.due_date,
        amountDue: row.amount_due,
        href: `/purchases/bills/${row.bill_id}`,
      }))
    : [];
  const lowStockRows = lowStock.ok ? lowStock.data.rows : [];

  return (
    <>
      <PageHeader
        title="Dashboard"
        description={`${session.organization.name} · ${fiscal.label} to date`}
        actions={<QuickActions can={can} />}
      />

      <PageBody>
        <StatGrid columns={6}>
          <StatCard
            label="Revenue"
            value={totals ? <Money value={totals.revenue} /> : "—"}
            hint={fiscal.label}
            href="/reports/profit-loss"
          />
          {/*
            The engine's own operating-expenses total, not a browser sum of
            several sections: the P&L returns no combined "expenses" figure,
            and adding one up here would put a UI-computed number on the
            dashboard as if it were a statement total.
          */}
          <StatCard
            label="Operating expenses"
            value={totals ? <Money value={totals.operating_expenses} /> : "—"}
            hint="Cost of goods sold is shown in the P&L"
            href="/reports/profit-loss"
          />
          <StatCard
            label="Net profit"
            value={totals ? <Money value={totals.net_profit} /> : "—"}
            tone={totals && money(totals.net_profit).lt(0) ? "negative" : "positive"}
            href="/reports/profit-loss"
          />
          <StatCard
            label="Receivables"
            value={receivableTotal ? <Money value={receivableTotal} /> : "—"}
            hint="Owed to you"
            href="/reports/receivables/ageing"
          />
          <StatCard
            label="Payables"
            value={payableTotal ? <Money value={payableTotal} /> : "—"}
            hint="You owe"
            href="/reports/payables/ageing"
          />
          <StatCard
            label="Overdue"
            value={overdueInvoiceRows.length}
            hint="Invoices past due"
            tone={overdueInvoiceRows.length > 0 ? "negative" : "default"}
            href="/reports/receivables/overdue-invoices"
          />
        </StatGrid>

        <div className="grid gap-4 lg:grid-cols-2">
          <AgeingCard
            title="Receivables ageing"
            report={receivables.ok ? receivables.data : null}
            href="/reports/receivables/ageing"
          />
          <AgeingCard
            title="Payables ageing"
            report={payables.ok ? payables.data : null}
            href="/reports/payables/ageing"
          />
        </div>

        <div className="grid gap-4 lg:grid-cols-2">
          <Card>
            <CardHeader
              title="Overdue invoices"
              description="Past their due date and still unpaid."
              actions={
                <Link
                  href="/reports/receivables/overdue-invoices"
                  className="text-xs font-medium text-brand-700 hover:underline"
                >
                  View all
                </Link>
              }
            />
            <CardBody className="p-0">
              <OverdueList
                rows={overdueInvoiceRows}
                today={today}
                emptyMessage="Nothing overdue. "
              />
            </CardBody>
          </Card>

          <Card>
            <CardHeader
              title="Bills due"
              description="Vendor bills past their due date."
              actions={
                <Link
                  href="/reports/payables/overdue-bills"
                  className="text-xs font-medium text-brand-700 hover:underline"
                >
                  View all
                </Link>
              }
            />
            <CardBody className="p-0">
              <OverdueList
                rows={overdueBillRows}
                today={today}
                emptyMessage="No bills overdue. "
              />
            </CardBody>
          </Card>
        </div>

        {can(PERMISSIONS.VIEW_INVENTORY) ? (
          <Section
            title="Low stock"
            description="Items at or below their reorder level."
            actions={
              <Link
                href="/inventory?low_stock_only=true"
                className="text-xs font-medium text-brand-700 hover:underline"
              >
                View all
              </Link>
            }
          >
            <Card>
              <CardBody className="p-0">
                {lowStockRows.length === 0 ? (
                  <EmptyState
                    compact
                    title="Nothing below reorder level"
                    description="Stock positions are healthy."
                  />
                ) : (
                  <ul className="divide-y divide-ink-100">
                    {lowStockRows.slice(0, 8).map((row) => (
                      <li
                        key={row.item_id}
                        className="flex items-center justify-between gap-3 px-4 py-2.5"
                      >
                        <div className="min-w-0">
                          <Link
                            href={`/items/${row.item_id}`}
                            className="block truncate text-sm font-medium text-brand-700 hover:underline"
                          >
                            {row.item_name}
                          </Link>
                          <span className="tabular text-xs text-ink-500">{row.item_sku}</span>
                        </div>
                        <div className="shrink-0 text-right">
                          {/* Low stock is organization-wide: the report sums every warehouse. */}
                          <Quantity value={row.on_hand} className="text-sm text-danger-600" />
                          <p className="text-2xs text-ink-500">
                            reorder at <Quantity value={row.reorder_level} />
                          </p>
                        </div>
                      </li>
                    ))}
                  </ul>
                )}
              </CardBody>
            </Card>
          </Section>
        ) : null}
      </PageBody>
    </>
  );
}

function AgeingCard({
  title,
  report,
  href,
}: {
  title: string;
  report: AgeingReport | null;
  href: string;
}) {
  return (
    <Card>
      <CardHeader
        title={title}
        actions={
          <Link href={appHref(href)} className="text-xs font-medium text-brand-700 hover:underline">
            Full report
          </Link>
        }
      />
      <CardBody>
        {!report ? (
          <p className="text-sm text-ink-500">Not available.</p>
        ) : (
          <dl className="grid grid-cols-2 gap-x-4 gap-y-2 sm:grid-cols-3">
            {AGEING_BUCKETS.map((bucket, index) => (
              <div key={bucket}>
                <dt className="text-2xs font-medium tracking-wide text-ink-500 uppercase">
                  {AGEING_BUCKET_LABELS[bucket]}
                </dt>
                <dd className="mt-0.5">
                  <Money
                    value={report.totals[bucket] ?? "0"}
                    colorNegative={false}
                    // Older buckets are worse news. The label already says
                    // which bucket it is; colour only reinforces it.
                    className={index >= 3 ? "text-warning-700" : undefined}
                  />
                </dd>
              </div>
            ))}
            <div className="col-span-2 border-t border-ink-200 pt-2 sm:col-span-3">
              <dt className="text-2xs font-medium tracking-wide text-ink-500 uppercase">Total</dt>
              <dd className="mt-0.5">
                <Money value={report.grand_total} strong colorNegative={false} />
              </dd>
            </div>
          </dl>
        )}
      </CardBody>
    </Card>
  );
}

/** An overdue invoice or bill, normalised from the two reports' different keys. */
interface OverdueRow {
  id: string;
  number: string;
  party: string;
  dueDate: string;
  amountDue: string;
  href: string;
}

function OverdueList({
  rows,
  today,
  emptyMessage,
}: {
  rows: OverdueRow[];
  today: string;
  emptyMessage: string;
}) {
  if (rows.length === 0) {
    return <EmptyState compact title={emptyMessage} />;
  }

  return (
    <ul className="divide-y divide-ink-100">
      {rows.slice(0, 6).map((row) => {
        // The overdue reports carry no days_overdue (only the ageing rows
        // do), so this is calendar arithmetic on the due date — not a
        // financial calculation.
        const overdueDays = daysBetween(row.dueDate, today) ?? 0;
        return (
          <li key={row.id} className="flex items-center justify-between gap-3 px-4 py-2.5">
            <div className="min-w-0">
              <Link
                href={appHref(row.href)}
                className="block truncate text-sm font-medium text-brand-700 hover:underline"
              >
                {row.number || "—"}
              </Link>
              <span className="block truncate text-xs text-ink-500">
                {row.party || "—"} · due {formatDate(row.dueDate)}
              </span>
            </div>
            <div className="flex shrink-0 items-center gap-2">
              <Badge tone="danger" marker size="sm">
                {overdueDays}d
              </Badge>
              <Money value={row.amountDue} className="text-sm" />
            </div>
          </li>
        );
      })}
    </ul>
  );
}

/** Quick actions, filtered to what the role may actually do (spec §65). */
function QuickActions({ can }: { can: (permission: Permission) => boolean }) {
  const actions = [
    { label: "New invoice", href: "/sales/invoices/new", permission: PERMISSIONS.CREATE_INVOICE },
    { label: "Record expense", href: "/purchases/expenses/new", permission: PERMISSIONS.MANAGE_EXPENSES },
    { label: "New bill", href: "/purchases/bills/new", permission: PERMISSIONS.CREATE_BILL },
    { label: "Record payment", href: "/sales/payments/new", permission: PERMISSIONS.RECORD_PAYMENT },
  ].filter((action) => can(action.permission));

  if (actions.length === 0) return null;

  return (
    <>
      {actions.slice(0, 2).map((action, index) => (
        <Link
          key={action.href}
          href={appHref(action.href)}
          className={
            index === 0
              ? "inline-flex h-9 items-center gap-1.5 rounded-md border border-brand-700 bg-brand-700 px-3.5 text-sm font-medium text-white transition-colors hover:bg-brand-800"
              : "inline-flex h-9 items-center gap-1.5 rounded-md border border-ink-300 bg-white px-3.5 text-sm font-medium text-ink-800 transition-colors hover:bg-ink-50"
          }
        >
          {index === 0 ? <Icons.plus className="size-3.5" /> : null}
          {action.label}
        </Link>
      ))}
    </>
  );
}
