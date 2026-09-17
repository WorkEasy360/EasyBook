import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { PageHeader } from "@/components/ui/page-header";
import { DetailList, PageBody, Section } from "@/components/ui/detail";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { DataTable, type Column } from "@/components/ui/data-table";
import { LinkButton } from "@/components/ui/link-button";
import { Money, Quantity } from "@/components/ui/money";
import { StatCard, StatGrid } from "@/components/ui/stat-card";
import { RECURRING_FREQUENCY_LABELS } from "@/components/ui/status-badge";
import { ErrorState, ForbiddenState } from "@/components/ui/states";
import { DocumentAction } from "@/features/shared/document-action";
import { serverApi, tryServer } from "@/lib/api/server";
import { accountLabel, recordsById } from "@/lib/api/lookups";
import { wholeList } from "@/lib/list-query";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { ApiError, referenceOf } from "@/lib/api/errors";
import { formatDate, formatDateTime } from "@/lib/datetime";
import type { Account } from "@/types/api/accounting";
import type { Item } from "@/types/api/items";
import type { RecurringBillTemplate, RecurringBillTemplateLine, Vendor } from "@/types/api/purchases";

export const metadata: Metadata = { title: "Recurring bill" };

export default async function RecurringBillDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const session = await requireSession();
  const { id } = await params;
  const role = session.role;

  if (!roleHasPermission(role, PERMISSIONS.VIEW_RECURRING_BILLS)) {
    return (
      <>
        <PageHeader title="Recurring bill" />
        <ForbiddenState resource="recurring bills" />
      </>
    );
  }

  const result = await tryServer(() => serverApi.get<RecurringBillTemplate>(`purchases/recurring-bills/${id}`));
  if (!result.ok) {
    if (result.error instanceof ApiError && result.error.isNotFound) notFound();
    return (
      <>
        <PageHeader title="Recurring bill" />
        <ErrorState message={result.error.message} reference={referenceOf(result.error)} />
      </>
    );
  }

  const template = result.data;
  const selfHref = `/purchases/recurring-bills/${template.id}`;
  const canManage = roleHasPermission(role, PERMISSIONS.MANAGE_RECURRING_BILLS);
  const canViewAccounting = roleHasPermission(role, PERMISSIONS.VIEW_ACCOUNTING);

  const [vendor, items, accounts] = await Promise.all([
    roleHasPermission(role, PERMISSIONS.VIEW_VENDORS)
      ? tryServer(() => serverApi.get<Vendor>(`purchases/vendors/${template.vendor}`))
      : Promise.resolve(null),
    recordsById<Item>("items", template.lines.map((line) => line.item)),
    canViewAccounting
      ? recordsById<Account>("accounting/accounts", [
          template.payable_account,
          template.tax_recoverable_account,
          ...template.lines.map((line) => line.expense_account),
        ])
      : Promise.resolve(new Map<string, Account>()),
  ]);

  const vendorName = vendor?.ok ? vendor.data.display_name : null;
  const frequency = RECURRING_FREQUENCY_LABELS[template.frequency] ?? template.frequency;
  const title = vendorName ? `${vendorName} · ${frequency}` : `Recurring bill · ${frequency}`;
  const ended = template.end_date !== null && template.next_run_at > template.end_date;

  const columns: Column<RecurringBillTemplateLine>[] = [
    { key: "line_number", header: "#", width: "2.5rem", cell: (line) => line.line_number },
    {
      key: "item",
      header: "Item",
      cell: (line) => {
        const item = items.get(line.item);
        return (
          <div className="min-w-0">
            <Link href={`/items/${line.item}`} className="font-medium text-brand-700 hover:underline">
              {line.description || item?.name || "Item"}
            </Link>
            {item ? <p className="tabular text-xs text-ink-500">{item.sku}</p> : null}
          </div>
        );
      },
    },
    { key: "quantity", header: "Qty", numeric: true, cell: (line) => <Quantity value={line.quantity} /> },
    { key: "unit_price", header: "Rate", numeric: true, cell: (line) => <Money value={line.unit_price} currency={template.currency} /> },
    { key: "discount", header: "Discount", numeric: true, hideBelow: "md", cell: (line) => `${line.discount_percent}%` },
    { key: "tax", header: "Tax rate", numeric: true, hideBelow: "sm", cell: (line) => `${line.tax_rate}%` },
    ...(canViewAccounting
      ? [
          {
            key: "account",
            header: "Expense account",
            hideBelow: "lg",
            cell: (line: RecurringBillTemplateLine) =>
              line.expense_account ? accountLabel(accounts, line.expense_account) : <span className="text-ink-500">Item&apos;s purchase account</span>,
          } satisfies Column<RecurringBillTemplateLine>,
        ]
      : []),
  ];

  return (
    <>
      <PageHeader
        title={title}
        breadcrumbs={[{ label: "Recurring bills", href: "/purchases/recurring-bills" }, { label: title }]}
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
                  resource="purchases/recurring-bills"
                  id={template.id}
                  action="deactivate"
                  label="Deactivate"
                  confirmTitle="Deactivate this recurring bill?"
                  confirmMessage={
                    <p>
                      No more bills are generated until it is activated again. Bills already generated are not changed.
                    </p>
                  }
                  successTitle="Recurring bill deactivated"
                />
              ) : (
                <DocumentAction
                  resource="purchases/recurring-bills"
                  id={template.id}
                  action="activate"
                  label="Activate"
                  variant="primary"
                  confirmTitle="Activate this recurring bill?"
                  confirmMessage={
                    <p>
                      A draft bill will be generated on {formatDate(template.next_run_at)} and then {frequency.toLowerCase()}
                      {template.end_date ? ` until ${formatDate(template.end_date)}` : ""}. If that date has already passed,
                      the next scheduled run generates it. Generated bills are drafts and post nothing until reviewed.
                    </p>
                  }
                  successTitle="Recurring bill activated"
                />
              )}
            </>
          ) : null
        }
      />

      <PageBody>
        <StatGrid columns={3}>
          <StatCard
            label="Next bill"
            value={template.is_active && !ended ? formatDate(template.next_run_at) : "None scheduled"}
            hint={!template.is_active ? "Inactive" : ended ? "Past the end date" : `Then ${frequency.toLowerCase()}`}
          />
          <StatCard label="Repeats" value={frequency} hint={`Started ${formatDate(template.start_date)}`} />
          <StatCard label="Due after" value={`${template.due_days} days`} hint="From each bill date" />
        </StatGrid>

        <div className="grid gap-4 lg:grid-cols-3">
          <Card className="lg:col-span-2">
            <CardHeader title="Details" />
            <CardBody>
              <DetailList
                items={[
                  {
                    label: "Vendor",
                    value: (
                      <Link href={`/purchases/vendors/${template.vendor}`} className="text-brand-700 hover:underline">
                        {vendorName ?? "View vendor"}
                      </Link>
                    ),
                  },
                  { label: "Start date", value: formatDate(template.start_date) },
                  { label: "End date", value: template.end_date ? formatDate(template.end_date) : "Runs until deactivated" },
                  { label: "Currency", value: template.currency },
                  ...(canViewAccounting
                    ? [
                        { label: "Payable account", value: accountLabel(accounts, template.payable_account) },
                        { label: "Tax recoverable account", value: accountLabel(accounts, template.tax_recoverable_account, "None") },
                      ]
                    : []),
                  { label: "Created", value: formatDateTime(template.created_at, { timeZone: session.timeZone }) },
                  ...(template.notes ? [{ label: "Notes", value: <span className="whitespace-pre-line">{template.notes}</span>, span: true }] : []),
                ]}
              />
            </CardBody>
          </Card>
          <Card>
            <CardHeader title="How it runs" />
            <CardBody className="text-sm text-ink-700">
              <p>
                On each run date a scheduled job creates a draft bill from these lines, priced by the accounting engine
                at that moment. Find generated bills under{" "}
                <Link href={`/purchases/bills?vendor=${template.vendor}&status=draft`} className="text-brand-700 hover:underline">
                  this vendor&apos;s draft bills
                </Link>
                .
              </p>
            </CardBody>
          </Card>
        </div>

        <Section title="Lines" description="The inputs each generated bill starts from. Amounts are calculated when a bill is generated.">
          <DataTable
            caption="Recurring bill lines"
            columns={columns}
            data={wholeList(template.lines)}
            getRowId={(line) => line.id}
            emptyTitle="No lines"
            page={1}
            pageSize={Math.max(template.lines.length, 1)}
            buildPageHref={() => selfHref}
          />
        </Section>
      </PageBody>
    </>
  );
}
