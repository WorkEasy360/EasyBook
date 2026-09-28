import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { PageHeader } from "@/components/ui/page-header";
import { DetailList, PageBody, Section } from "@/components/ui/detail";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { DataTable, type Column } from "@/components/ui/data-table";
import { Badge } from "@/components/ui/badge";
import { LinkButton } from "@/components/ui/link-button";
import { Money, Quantity } from "@/components/ui/money";
import { RECURRING_FREQUENCY_LABELS } from "@/components/ui/status-badge";
import { ErrorState, ForbiddenState } from "@/components/ui/states";
import { DocumentAction } from "@/features/shared/document-action";
import { PartyCard } from "@/features/documents/document-view";
import { serverApi, tryServer } from "@/lib/api/server";
import { accountLabel, recordsById } from "@/lib/api/lookups";
import { wholeList } from "@/lib/list-query";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { ApiError, referenceOf } from "@/lib/api/errors";
import { formatDate, formatDateTime, todayInZone } from "@/lib/datetime";
import type { Account } from "@/types/api/accounting";
import type { Item } from "@/types/api/items";
import type { Warehouse } from "@/types/api/inventory";
import type { Customer, RecurringInvoiceTemplate, RecurringInvoiceTemplateLine } from "@/types/api/sales";

export const metadata: Metadata = { title: "Recurring invoice" };

/**
 * A template's lines carry inputs only — no computed amounts are serialized
 * for them (RecurringInvoiceTemplateLineSerializer). Each generated invoice
 * is priced by the engine when it is created, so this page shows no totals.
 *
 * The invoices a template has generated (RecurringInvoiceRun) are not exposed
 * by any endpoint, so they cannot be listed here.
 */
export default async function RecurringInvoiceDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const session = await requireSession();
  const { id } = await params;
  const role = session.role;

  if (!roleHasPermission(role, PERMISSIONS.VIEW_RECURRING_INVOICES)) {
    return (
      <>
        <PageHeader title="Recurring invoice" />
        <ForbiddenState resource="recurring invoices" />
      </>
    );
  }

  const result = await tryServer(() => serverApi.get<RecurringInvoiceTemplate>(`sales/recurring-invoices/${id}`));
  if (!result.ok) {
    if (result.error instanceof ApiError && result.error.isNotFound) notFound();
    return (
      <>
        <PageHeader title="Recurring invoice" />
        <ErrorState title="Could not load the recurring invoice" message={result.error.message} reference={referenceOf(result.error)} />
      </>
    );
  }

  const template = result.data;
  const canManage = roleHasPermission(role, PERMISSIONS.MANAGE_RECURRING_INVOICES);
  const canViewAccounting = roleHasPermission(role, PERMISSIONS.VIEW_ACCOUNTING);

  const [customer, items, accounts, warehouse] = await Promise.all([
    roleHasPermission(role, PERMISSIONS.VIEW_CUSTOMERS)
      ? tryServer(() => serverApi.get<Customer>(`sales/customers/${template.customer}`))
      : Promise.resolve(null),
    recordsById<Item>("items", template.lines.map((line) => line.item)),
    canViewAccounting
      ? recordsById<Account>("accounting/accounts", [template.receivable_account, template.tax_payable_account])
      : Promise.resolve(new Map<string, Account>()),
    template.warehouse && roleHasPermission(role, PERMISSIONS.VIEW_INVENTORY)
      ? tryServer(() => serverApi.get<Warehouse>(`inventory/warehouses/${template.warehouse}`))
      : Promise.resolve(null),
  ]);

  const customerName = customer?.ok ? customer.data.display_name : null;
  const frequency = RECURRING_FREQUENCY_LABELS[template.frequency] ?? template.frequency;
  const title = customerName ? `${frequency} invoice · ${customerName}` : `${frequency} recurring invoice`;
  const today = todayInZone(session.timeZone);
  const ended = template.end_date !== null && template.next_run_at > template.end_date;
  const behind = !ended && template.next_run_at < today;

  const columns: Column<RecurringInvoiceTemplateLine>[] = [
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
    {
      key: "unit_price",
      header: "Rate",
      numeric: true,
      cell: (line) => <Money value={line.unit_price} currency={template.currency} />,
    },
    { key: "discount", header: "Discount", numeric: true, hideBelow: "sm", cell: (line) => `${line.discount_percent}%` },
    { key: "tax", header: "Tax rate", numeric: true, cell: (line) => `${line.tax_rate}%` },
  ];

  return (
    <>
      <PageHeader
        title={title}
        breadcrumbs={[{ label: "Recurring invoices", href: "/sales/recurring-invoices" }, { label: "Template" }]}
        meta={
          <>
            <Badge tone={template.is_active ? "success" : "neutral"} marker={template.is_active}>
              {template.is_active ? "Active" : "Inactive"}
            </Badge>
            {ended ? (
              <Badge tone="neutral" size="sm">
                Past end date
              </Badge>
            ) : null}
          </>
        }
        actions={
          canManage ? (
            <>
              <LinkButton href={`/sales/recurring-invoices/${template.id}/edit`}>Edit</LinkButton>
              {template.is_active ? (
                <DocumentAction
                  resource="sales/recurring-invoices"
                  id={template.id}
                  action="deactivate"
                  label="Deactivate"
                  variant="danger"
                  confirmTitle="Deactivate this recurring invoice?"
                  confirmMessage={
                    <>
                      <p>No more draft invoices are generated from it. Invoices already generated are not affected.</p>
                      <p className="mt-2">
                        The next invoice date stays at {formatDate(template.next_run_at)}. If you reactivate later, a
                        draft is generated for every occurrence missed in the meantime.
                      </p>
                    </>
                  }
                  successTitle="Recurring invoice deactivated"
                />
              ) : (
                <DocumentAction
                  resource="sales/recurring-invoices"
                  id={template.id}
                  action="activate"
                  label="Activate"
                  variant="primary"
                  confirmTitle="Activate this recurring invoice?"
                  confirmMessage={
                    <>
                      <p>
                        The scheduler creates a draft invoice on each {frequency.toLowerCase()} date, starting{" "}
                        {formatDate(template.next_run_at)}
                        {template.end_date ? ` and ending by ${formatDate(template.end_date)}` : ""}. Drafts post nothing
                        until someone posts them.
                      </p>
                      {template.next_run_at < today && !ended ? (
                        <p className="mt-2 text-warning-700">
                          That date has passed, so on its next run the scheduler catches up with one draft per missed
                          occurrence up to today.
                        </p>
                      ) : null}
                    </>
                  }
                  successTitle="Recurring invoice activated"
                />
              )}
            </>
          ) : null
        }
      />

      <PageBody>
        <div className="grid gap-4 lg:grid-cols-3">
          <PartyCard
            title="Customer"
            href={`/sales/customers/${template.customer}`}
            name={customerName}
            code={customer?.ok ? customer.data.customer_code : null}
            gstin={customer?.ok ? customer.data.gstin : null}
            address={customer?.ok ? customer.data.billing_address : null}
          />
          <Card className="lg:col-span-2">
            <CardHeader title="Schedule" />
            <CardBody>
              <DetailList
                items={[
                  { label: "Repeats", value: frequency },
                  {
                    label: "Next invoice",
                    value: ended ? (
                      "None — past the end date"
                    ) : (
                      <span>
                        {formatDate(template.next_run_at)}
                        {behind && template.is_active ? (
                          <span className="block text-xs text-ink-500">Due now; generated on the scheduler&apos;s next hourly run.</span>
                        ) : null}
                      </span>
                    ),
                  },
                  { label: "Start date", value: formatDate(template.start_date) },
                  { label: "End date", value: template.end_date ? formatDate(template.end_date) : "No end date" },
                  { label: "Due after", value: `${template.due_days} ${template.due_days === 1 ? "day" : "days"}` },
                  { label: "Reference", value: template.reference || "—" },
                  { label: "Currency", value: template.currency },
                  ...(canViewAccounting
                    ? [
                        { label: "Receivable account", value: accountLabel(accounts, template.receivable_account) },
                        { label: "Tax payable account", value: accountLabel(accounts, template.tax_payable_account, "None") },
                      ]
                    : []),
                  { label: "Warehouse", value: warehouse?.ok ? warehouse.data.name : template.warehouse ? "Configured" : "None" },
                  { label: "Last changed", value: formatDateTime(template.updated_at, { timeZone: session.timeZone }) },
                ]}
              />
            </CardBody>
          </Card>
        </div>

        <Section
          title="Lines"
          description="Copied to every generated invoice. Amounts and tax are calculated on each invoice when it is created."
        >
          <DataTable
            caption="Recurring invoice lines"
            columns={columns}
            data={wholeList(template.lines)}
            getRowId={(line) => line.id}
            emptyTitle="No lines"
            page={1}
            pageSize={Math.max(template.lines.length, 1)}
            buildPageHref={() => `/sales/recurring-invoices/${template.id}`}
          />
        </Section>

        {template.notes || template.terms ? (
          <Card>
            <CardHeader title="Notes and terms" />
            <CardBody>
              <DetailList
                columns={1}
                items={[
                  ...(template.notes ? [{ label: "Notes", value: <span className="whitespace-pre-line">{template.notes}</span> }] : []),
                  ...(template.terms ? [{ label: "Terms", value: <span className="whitespace-pre-line">{template.terms}</span> }] : []),
                ]}
              />
            </CardBody>
          </Card>
        ) : null}

        <p className="text-xs text-ink-500">
          Generated invoices appear as drafts in{" "}
          <Link href={`/sales/invoices?customer=${template.customer}&status=draft`} className="text-brand-700 hover:underline">
            this customer&apos;s draft invoices
          </Link>
          .
        </p>
      </PageBody>
    </>
  );
}
