import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { PageHeader } from "@/components/ui/page-header";
import { DetailList, PageBody } from "@/components/ui/detail";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { LinkButton } from "@/components/ui/link-button";
import { Money } from "@/components/ui/money";
import { StatCard, StatGrid } from "@/components/ui/stat-card";
import { RECURRING_FREQUENCY_LABELS } from "@/components/ui/status-badge";
import { ErrorState, ForbiddenState } from "@/components/ui/states";
import { DocumentAction } from "@/features/shared/document-action";
import { serverApi, tryServer } from "@/lib/api/server";
import { accountLabel, recordsById } from "@/lib/api/lookups";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { ApiError, referenceOf } from "@/lib/api/errors";
import { formatDate, formatDateTime } from "@/lib/datetime";
import type { Account } from "@/types/api/accounting";
import type { RecurringExpenseTemplate, Vendor } from "@/types/api/purchases";

export const metadata: Metadata = { title: "Recurring expense" };

export default async function RecurringExpenseDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const session = await requireSession();
  const { id } = await params;
  const role = session.role;

  if (!roleHasPermission(role, PERMISSIONS.VIEW_RECURRING_BILLS)) {
    return (
      <>
        <PageHeader title="Recurring expense" />
        <ForbiddenState resource="recurring expenses" />
      </>
    );
  }

  const result = await tryServer(() => serverApi.get<RecurringExpenseTemplate>(`purchases/recurring-expenses/${id}`));
  if (!result.ok) {
    if (result.error instanceof ApiError && result.error.isNotFound) notFound();
    return (
      <>
        <PageHeader title="Recurring expense" />
        <ErrorState message={result.error.message} reference={referenceOf(result.error)} />
      </>
    );
  }

  const template = result.data;
  const selfHref = `/purchases/recurring-expenses/${template.id}`;
  const canManage = roleHasPermission(role, PERMISSIONS.MANAGE_RECURRING_BILLS);
  const canViewAccounting = roleHasPermission(role, PERMISSIONS.VIEW_ACCOUNTING);

  const [vendor, accounts] = await Promise.all([
    template.vendor && roleHasPermission(role, PERMISSIONS.VIEW_VENDORS)
      ? tryServer(() => serverApi.get<Vendor>(`purchases/vendors/${template.vendor}`))
      : Promise.resolve(null),
    canViewAccounting
      ? recordsById<Account>("accounting/accounts", [
          template.expense_account,
          template.paid_through_account,
          template.tax_recoverable_account,
        ])
      : Promise.resolve(new Map<string, Account>()),
  ]);

  const frequency = RECURRING_FREQUENCY_LABELS[template.frequency] ?? template.frequency;
  const title = template.description ? `${template.description} · ${frequency}` : `Recurring expense · ${frequency}`;
  const ended = template.end_date !== null && template.next_run_at > template.end_date;

  return (
    <>
      <PageHeader
        title={title}
        breadcrumbs={[{ label: "Recurring expenses", href: "/purchases/recurring-expenses" }, { label: title }]}
        meta={
          <Badge tone={template.is_active ? "success" : "neutral"} marker={template.is_active}>
            {template.is_active ? "Active" : "Inactive"}
          </Badge>
        }
        {...(template.reference ? { description: template.reference } : {})}
        actions={
          canManage ? (
            <>
              <LinkButton href={`${selfHref}/edit`}>Edit</LinkButton>
              {template.is_active ? (
                <DocumentAction
                  resource="purchases/recurring-expenses"
                  id={template.id}
                  action="deactivate"
                  label="Deactivate"
                  confirmTitle="Deactivate this recurring expense?"
                  confirmMessage={
                    <p>No more expenses are generated until it is activated again. Expenses already generated are not changed.</p>
                  }
                  successTitle="Recurring expense deactivated"
                />
              ) : (
                <DocumentAction
                  resource="purchases/recurring-expenses"
                  id={template.id}
                  action="activate"
                  label="Activate"
                  variant="primary"
                  confirmTitle="Activate this recurring expense?"
                  confirmMessage={
                    <p>
                      A draft expense will be generated on {formatDate(template.next_run_at)} and then {frequency.toLowerCase()}
                      {template.end_date ? ` until ${formatDate(template.end_date)}` : ""}. If that date has already passed, the
                      next scheduled run generates it. Generated expenses are drafts and post nothing until reviewed.
                    </p>
                  }
                  successTitle="Recurring expense activated"
                />
              )}
            </>
          ) : null
        }
      />

      <PageBody>
        <StatGrid columns={3}>
          <StatCard
            label="Next expense"
            value={template.is_active && !ended ? formatDate(template.next_run_at) : "None scheduled"}
            hint={!template.is_active ? "Inactive" : ended ? "Past the end date" : `Then ${frequency.toLowerCase()}`}
          />
          <StatCard
            label="Amount"
            value={<Money value={template.amount} currency={template.currency} />}
            hint="Before tax, each occurrence"
          />
          <StatCard
            label="Tax rate"
            value={`${template.tax_rate}%`}
            hint="Tax and total are calculated on each generated expense"
          />
        </StatGrid>

        <Card>
          <CardHeader title="Details" />
          <CardBody>
            <DetailList
              columns={3}
              items={[
                { label: "Description", value: template.description || "—" },
                {
                  label: "Vendor",
                  value: template.vendor ? (
                    <Link href={`/purchases/vendors/${template.vendor}`} className="text-brand-700 hover:underline">
                      {vendor?.ok ? vendor.data.display_name : "View vendor"}
                    </Link>
                  ) : (
                    "None"
                  ),
                },
                { label: "Repeats", value: frequency },
                { label: "Start date", value: formatDate(template.start_date) },
                { label: "End date", value: template.end_date ? formatDate(template.end_date) : "Runs until deactivated" },
                { label: "Currency", value: template.currency },
                ...(canViewAccounting
                  ? [
                      { label: "Expense account", value: accountLabel(accounts, template.expense_account) },
                      { label: "Paid through", value: accountLabel(accounts, template.paid_through_account) },
                      { label: "Tax recoverable account", value: accountLabel(accounts, template.tax_recoverable_account, "None") },
                    ]
                  : []),
                { label: "Created", value: formatDateTime(template.created_at, { timeZone: session.timeZone }) },
                ...(template.notes ? [{ label: "Notes", value: <span className="whitespace-pre-line">{template.notes}</span>, span: true }] : []),
              ]}
            />
          </CardBody>
        </Card>

        <p className="text-xs text-ink-500">
          Generated expenses appear as drafts under{" "}
          <Link href="/purchases/expenses?status=draft" className="text-brand-700 hover:underline">
            draft expenses
          </Link>
          , where they are reviewed and posted.
        </p>
      </PageBody>
    </>
  );
}
