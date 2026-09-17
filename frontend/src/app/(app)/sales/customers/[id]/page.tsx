import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { PageHeader } from "@/components/ui/page-header";
import { PageBody, DetailList, Section } from "@/components/ui/detail";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Money } from "@/components/ui/money";
import { StatCard, StatGrid } from "@/components/ui/stat-card";
import { DataTable, type Column } from "@/components/ui/data-table";
import { StatusBadge, INVOICE_STATUS } from "@/components/ui/status-badge";
import { ErrorState, ForbiddenState } from "@/components/ui/states";
import { serverApi, tryServer } from "@/lib/api/server";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { ApiError, referenceOf } from "@/lib/api/errors";
import { formatDate } from "@/lib/datetime";
import type { Address, Customer, Invoice } from "@/types/api/sales";
import type { Paginated } from "@/lib/api/types";

export const metadata: Metadata = { title: "Customer" };

export default async function CustomerDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const session = await requireSession();
  const { id } = await params;

  if (!roleHasPermission(session.role, PERMISSIONS.VIEW_CUSTOMERS)) {
    return (
      <>
        <PageHeader title="Customer" />
        <ForbiddenState resource="customers" />
      </>
    );
  }

  const canManage = roleHasPermission(session.role, PERMISSIONS.MANAGE_CUSTOMERS);
  const canViewInvoices = roleHasPermission(session.role, PERMISSIONS.VIEW_INVOICES);

  /*
   * Fetched in parallel, not in sequence: the invoice list does not depend on
   * the customer, and awaiting them one after the other would double the time
   * to first byte for no reason (spec §77).
   */
  const [customerResult, invoicesResult] = await Promise.all([
    tryServer(() => serverApi.get<Customer>(`sales/customers/${id}`)),
    canViewInvoices
      ? tryServer(() =>
          serverApi.list<Invoice>("sales/invoices", { query: { customer: id, page_size: 10 } }),
        )
      : Promise.resolve({ ok: false as const, error: new Error("forbidden") }),
  ]);

  if (!customerResult.ok) {
    const error = customerResult.error;
    if (error instanceof ApiError && error.isNotFound) notFound();
    return (
      <>
        <PageHeader title="Customer" />
        <ErrorState message={error.message} reference={referenceOf(error)} />
      </>
    );
  }

  const customer = customerResult.data;
  const invoices: Paginated<Invoice> | undefined = invoicesResult.ok
    ? invoicesResult.data
    : undefined;

  const invoiceColumns: Column<Invoice>[] = [
    {
      key: "invoice_number",
      header: "Invoice",
      cell: (invoice) => <span className="tabular">{invoice.invoice_number}</span>,
    },
    {
      key: "invoice_date",
      header: "Date",
      cell: (invoice) => formatDate(invoice.invoice_date),
    },
    {
      key: "due_date",
      header: "Due",
      hideBelow: "sm",
      cell: (invoice) => formatDate(invoice.due_date),
    },
    {
      key: "status",
      header: "Status",
      cell: (invoice) => <StatusBadge status={invoice.status} map={INVOICE_STATUS} size="sm" />,
    },
    {
      key: "total",
      header: "Total",
      numeric: true,
      cell: (invoice) => <Money value={invoice.total} currency={invoice.currency} />,
    },
    {
      key: "amount_due",
      header: "Due",
      numeric: true,
      cell: (invoice) => (
        <Money value={invoice.amount_due} currency={invoice.currency} strong />
      ),
    },
  ];

  return (
    <>
      <PageHeader
        title={customer.display_name}
        breadcrumbs={[
          { label: "Customers", href: "/sales/customers" },
          { label: customer.display_name },
        ]}
        meta={
          <Badge tone={customer.is_active ? "success" : "neutral"} marker={customer.is_active}>
            {customer.is_active ? "Active" : "Inactive"}
          </Badge>
        }
        description={
          <span className="tabular">
            {customer.customer_code}
            {customer.gstin ? ` · GSTIN ${customer.gstin}` : ""}
          </span>
        }
        actions={
          <>
            {canManage ? (
              <Link
                href={`/sales/customers/${customer.id}/edit`}
                className="inline-flex h-9 items-center rounded-md border border-ink-300 bg-white px-3.5 text-sm font-medium text-ink-800 transition-colors hover:bg-ink-50"
              >
                Edit
              </Link>
            ) : null}
            {roleHasPermission(session.role, PERMISSIONS.CREATE_INVOICE) ? (
              <Link
                href={`/sales/invoices/new?customer=${customer.id}`}
                className="inline-flex h-9 items-center rounded-md border border-brand-700 bg-brand-700 px-3.5 text-sm font-medium text-white transition-colors hover:bg-brand-800"
              >
                New invoice
              </Link>
            ) : null}
          </>
        }
      />

      <PageBody>
        {/*
          Outstanding is summed from the page of invoices actually loaded, so
          it is labelled as such rather than presented as the customer's
          balance. The authoritative figure is the AR report — the backend has
          no per-customer balance field on this serializer.
        */}
        {invoices ? (
          <StatGrid columns={3}>
            <StatCard
              label="Open invoices"
              value={invoices.results.filter((invoice) => invoice.status !== "paid" && invoice.status !== "void").length}
            />
            <StatCard
              label="Overdue invoices"
              value={invoices.results.filter((invoice) => invoice.status === "overdue").length}
              tone={invoices.results.some((invoice) => invoice.status === "overdue") ? "negative" : "default"}
            />
            <StatCard
              label="Payment terms"
              value={`Net ${customer.payment_terms_days}`}
              hint="Days from invoice date"
            />
          </StatGrid>
        ) : null}

        <div className="grid gap-4 lg:grid-cols-3">
          <Card className="lg:col-span-2">
            <CardHeader title="Details" />
            <CardBody>
              <DetailList
                items={[
                  { label: "Legal name", value: customer.legal_name || "—" },
                  { label: "Currency", value: customer.currency },
                  {
                    label: "Email",
                    value: customer.email ? (
                      <a href={`mailto:${customer.email}`} className="text-brand-700 hover:underline">
                        {customer.email}
                      </a>
                    ) : (
                      "—"
                    ),
                  },
                  { label: "Phone", value: customer.phone || "—" },
                  { label: "GSTIN", value: customer.gstin || "—" },
                  { label: "PAN", value: customer.pan || "—" },
                  {
                    label: "Credit limit",
                    value: customer.credit_limit ? (
                      <Money value={customer.credit_limit} currency={customer.currency} />
                    ) : (
                      "No limit"
                    ),
                  },
                  { label: "Payment terms", value: `Net ${customer.payment_terms_days} days` },
                  ...(customer.notes ? [{ label: "Notes", value: customer.notes, span: true }] : []),
                ]}
              />
            </CardBody>
          </Card>

          <Card>
            <CardHeader title="Addresses" />
            <CardBody className="flex flex-col gap-4">
              <AddressBlock label="Billing" address={customer.billing_address} />
              <AddressBlock label="Shipping" address={customer.shipping_address} />
            </CardBody>
          </Card>
        </div>

        {canViewInvoices ? (
          <Section
            title="Recent invoices"
            actions={
              <Link
                href={`/sales/invoices?customer=${customer.id}`}
                className="text-xs font-medium text-brand-700 hover:underline"
              >
                View all
              </Link>
            }
          >
            <DataTable
              caption={`Recent invoices for ${customer.display_name}`}
              columns={invoiceColumns}
              data={invoices}
              error={invoicesResult.ok ? null : (invoicesResult.error as ApiError)}
              getRowId={(invoice) => invoice.id}
              getRowHref={(invoice) => `/sales/invoices/${invoice.id}`}
              emptyTitle="No invoices yet"
              emptyDescription="Invoices raised for this customer will appear here."
              page={1}
              pageSize={10}
              buildPageHref={() => `/sales/invoices?customer=${customer.id}`}
            />
          </Section>
        ) : null}
      </PageBody>
    </>
  );
}

function AddressBlock({ label, address }: { label: string; address: Address | null }) {
  const lines = address
    ? [
        address.line1,
        address.line2,
        [address.city, address.state].filter(Boolean).join(", "),
        address.postal_code,
        address.country,
      ].filter((line): line is string => Boolean(line && line.trim()))
    : [];

  return (
    <div>
      <p className="text-2xs font-medium tracking-wide text-ink-500 uppercase">{label}</p>
      {lines.length > 0 ? (
        <address className="mt-1 text-sm not-italic text-ink-800">
          {lines.map((line) => (
            <span key={line} className="block">
              {line}
            </span>
          ))}
          {address?.state_code ? (
            <span className="mt-1 block text-xs text-ink-500">
              State code {address.state_code}
            </span>
          ) : null}
        </address>
      ) : (
        <p className="mt-1 text-sm text-ink-400">Not set</p>
      )}
    </div>
  );
}
