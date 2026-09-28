import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { PageHeader } from "@/components/ui/page-header";
import { DetailList, PageBody } from "@/components/ui/detail";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { LinkButton } from "@/components/ui/link-button";
import { Money } from "@/components/ui/money";
import { PrintButton } from "@/components/ui/print-button";
import { StatCard, StatGrid } from "@/components/ui/stat-card";
import { EXPENSE_STATUS, StatusBadge } from "@/components/ui/status-badge";
import { ErrorState, ForbiddenState } from "@/components/ui/states";
import { DocumentAction } from "@/features/shared/document-action";
import { serverApi, tryServer } from "@/lib/api/server";
import { accountLabel, recordsById } from "@/lib/api/lookups";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { ApiError, referenceOf } from "@/lib/api/errors";
import { formatDate, formatDateTime } from "@/lib/datetime";
import { isZero } from "@/lib/money";
import type { Account } from "@/types/api/accounting";
import type { Project } from "@/types/api/projects";
import type { Customer } from "@/types/api/sales";
import type { Expense, Vendor } from "@/types/api/purchases";

export const metadata: Metadata = { title: "Expense" };

export default async function ExpenseDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const session = await requireSession();
  const { id } = await params;
  const role = session.role;

  if (!roleHasPermission(role, PERMISSIONS.VIEW_EXPENSES)) {
    return (
      <>
        <PageHeader title="Expense" />
        <ForbiddenState resource="expenses" />
      </>
    );
  }

  const result = await tryServer(() => serverApi.get<Expense>(`purchases/expenses/${id}`));
  if (!result.ok) {
    if (result.error instanceof ApiError && result.error.isNotFound) notFound();
    return (
      <>
        <PageHeader title="Expense" />
        <ErrorState message={result.error.message} reference={referenceOf(result.error)} />
      </>
    );
  }

  const expense = result.data;
  const isDraft = expense.status === "draft";
  const canManage = roleHasPermission(role, PERMISSIONS.MANAGE_EXPENSES);
  const canViewAccounting = roleHasPermission(role, PERMISSIONS.VIEW_ACCOUNTING);

  const [vendor, customer, project, accounts] = await Promise.all([
    expense.vendor && roleHasPermission(role, PERMISSIONS.VIEW_VENDORS)
      ? tryServer(() => serverApi.get<Vendor>(`purchases/vendors/${expense.vendor}`))
      : Promise.resolve(null),
    expense.customer && roleHasPermission(role, PERMISSIONS.VIEW_CUSTOMERS)
      ? tryServer(() => serverApi.get<Customer>(`sales/customers/${expense.customer}`))
      : Promise.resolve(null),
    expense.project && roleHasPermission(role, PERMISSIONS.VIEW_PROJECTS)
      ? tryServer(() => serverApi.get<Project>(`projects/${expense.project}`))
      : Promise.resolve(null),
    canViewAccounting
      ? recordsById<Account>("accounting/accounts", [
          expense.expense_account,
          expense.paid_through_account,
          expense.tax_recoverable_account,
        ])
      : Promise.resolve(new Map<string, Account>()),
  ]);

  const title = expense.expense_number || "Draft expense";
  const taxed = !isZero(expense.tax_amount);
  const paidThrough = accounts.get(expense.paid_through_account);

  return (
    <>
      <PageHeader
        title={title}
        breadcrumbs={[{ label: "Expenses", href: "/purchases/expenses" }, { label: title }]}
        meta={<StatusBadge status={expense.status} map={EXPENSE_STATUS} />}
        {...(expense.description ? { description: expense.description } : {})}
        actions={
          <>
            <PrintButton />
            {isDraft && canManage ? <LinkButton href={`/purchases/expenses/${expense.id}/edit`}>Edit</LinkButton> : null}
            {expense.status === "posted" && canManage ? (
              <DocumentAction
                resource="purchases/expenses"
                id={expense.id}
                action="void"
                label="Void"
                variant="danger"
                confirmTitle={`Void ${title}?`}
                confirmMessage={
                  <p>
                    Voiding posts a reversal of this expense&apos;s journal, dated today. The expense stays on record,
                    marked void. This cannot be undone.
                  </p>
                }
                reason={{ label: "Reason", hint: "Stored with the void for the audit trail." }}
                successTitle="Expense voided"
              />
            ) : null}
            {isDraft && canManage ? (
              <DocumentAction
                resource="purchases/expenses"
                id={expense.id}
                action="post"
                label="Post expense"
                variant="primary"
                confirmTitle="Post this expense?"
                confirmMessage={
                  <>
                    <p>
                      Posting records <Money value={expense.total} currency={expense.currency} /> in the ledger, dated{" "}
                      {formatDate(expense.expense_date)}: the expense account is debited with the amount
                      {taxed ? ", the tax recoverable account with the tax," : ""} and{" "}
                      {paidThrough ? `${paidThrough.code} · ${paidThrough.name}` : "the paid-through account"} is credited with
                      the total.
                    </p>
                    <p className="mt-2">It assigns the expense number and locks it. To correct it afterwards, void it.</p>
                  </>
                }
                successTitle="Expense posted"
              />
            ) : null}
          </>
        }
      />

      <PageBody className="print-document">
        <StatGrid columns={3}>
          <StatCard label="Amount" value={<Money value={expense.amount} currency={expense.currency} />} hint="Before tax" />
          <StatCard
            label="Tax"
            value={<Money value={expense.tax_amount} currency={expense.currency} />}
            hint={`${expense.tax_rate}%`}
          />
          <StatCard
            label="Total"
            value={<Money value={expense.total} currency={expense.currency} strong />}
            hint={isDraft ? "Calculated by the server when the draft was saved" : undefined}
          />
        </StatGrid>

        <div className="grid gap-4 lg:grid-cols-3">
          <Card className="lg:col-span-2">
            <CardHeader title="Details" />
            <CardBody>
              <DetailList
                items={[
                  { label: "Expense date", value: formatDate(expense.expense_date) },
                  { label: "Reference", value: expense.reference || "—" },
                  {
                    label: "Vendor",
                    value: expense.vendor ? (
                      <Link href={`/purchases/vendors/${expense.vendor}`} className="text-brand-700 hover:underline">
                        {vendor?.ok ? vendor.data.display_name : "View vendor"}
                      </Link>
                    ) : (
                      "None"
                    ),
                  },
                  { label: "Currency", value: expense.currency },
                  ...(canViewAccounting
                    ? [
                        { label: "Expense account", value: accountLabel(accounts, expense.expense_account) },
                        { label: "Paid through", value: accountLabel(accounts, expense.paid_through_account) },
                        { label: "Tax recoverable account", value: accountLabel(accounts, expense.tax_recoverable_account, "None") },
                      ]
                    : []),
                  {
                    label: "Journal",
                    value: expense.accounting_journal ? (
                      canViewAccounting ? (
                        <Link href={`/accounting/journals/${expense.accounting_journal}`} className="text-brand-700 hover:underline">
                          View journal
                        </Link>
                      ) : (
                        "Posted"
                      )
                    ) : (
                      "Not posted"
                    ),
                  },
                  { label: "Created", value: formatDateTime(expense.created_at, { timeZone: session.timeZone }) },
                  ...(expense.notes ? [{ label: "Notes", value: <span className="whitespace-pre-line">{expense.notes}</span>, span: true }] : []),
                ]}
              />
            </CardBody>
          </Card>

          <Card>
            <CardHeader title="Billing" />
            <CardBody>
              <DetailList
                columns={1}
                items={[
                  {
                    label: "Billable",
                    value: expense.is_billable ? (
                      <Badge tone="brand" size="sm">
                        Billable
                      </Badge>
                    ) : (
                      "No"
                    ),
                  },
                  {
                    label: "Customer",
                    value: expense.customer ? (
                      <Link href={`/sales/customers/${expense.customer}`} className="text-brand-700 hover:underline">
                        {customer?.ok ? customer.data.display_name : "View customer"}
                      </Link>
                    ) : (
                      "None"
                    ),
                  },
                  {
                    label: "Project",
                    value: expense.project ? (
                      <Link href={`/projects/${expense.project}`} className="text-brand-700 hover:underline">
                        {project?.ok ? project.data.name : "View project"}
                      </Link>
                    ) : (
                      "None"
                    ),
                  },
                ]}
              />
            </CardBody>
          </Card>
        </div>
      </PageBody>
    </>
  );
}
