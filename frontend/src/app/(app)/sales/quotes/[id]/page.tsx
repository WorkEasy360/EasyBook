import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { PageHeader } from "@/components/ui/page-header";
import { DetailList, PageBody, Section } from "@/components/ui/detail";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { LinkButton } from "@/components/ui/link-button";
import { PrintButton } from "@/components/ui/print-button";
import { Badge } from "@/components/ui/badge";
import { INVOICE_STATUS, QUOTE_STATUS, SALES_ORDER_STATUS, StatusBadge } from "@/components/ui/status-badge";
import { ErrorState, ForbiddenState } from "@/components/ui/states";
import { DocumentAction } from "@/features/shared/document-action";
import { DocumentTotalsCard, PartyCard, PricedLinesTable } from "@/features/documents/document-view";
import { QuoteConvertDialog } from "@/features/sales/quote-convert-dialog";
import { serverApi, tryServer } from "@/lib/api/server";
import { recordsById } from "@/lib/api/lookups";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { ApiError, referenceOf } from "@/lib/api/errors";
import { addDays, formatDate, todayInZone } from "@/lib/datetime";
import type { Item } from "@/types/api/items";
import type { Warehouse } from "@/types/api/inventory";
import type { Customer, Invoice, Quote, SalesOrder } from "@/types/api/sales";

export const metadata: Metadata = { title: "Quote" };

export default async function QuoteDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const session = await requireSession();
  const { id } = await params;
  const role = session.role;

  if (!roleHasPermission(role, PERMISSIONS.VIEW_QUOTES)) {
    return (
      <>
        <PageHeader title="Quote" />
        <ForbiddenState resource="quotes" />
      </>
    );
  }

  const result = await tryServer(() => serverApi.get<Quote>(`sales/quotes/${id}`));
  if (!result.ok) {
    if (result.error instanceof ApiError && result.error.isNotFound) notFound();
    return (
      <>
        <PageHeader title="Quote" />
        <ErrorState title="Could not load the quote" message={result.error.message} reference={referenceOf(result.error)} />
      </>
    );
  }

  const quote = result.data;
  const canManage = roleHasPermission(role, PERMISSIONS.MANAGE_QUOTES);
  const isAccepted = quote.status === "accepted";

  // The quote does not say what it was converted into; the order and invoice
  // each point back through `source_quote`. Neither list filters by it, so
  // one page of this customer's documents is scanned. Only an accepted quote
  // can have been converted.
  const [customer, items, orders, invoices, latestInvoice, warehouses] = await Promise.all([
    roleHasPermission(role, PERMISSIONS.VIEW_CUSTOMERS)
      ? tryServer(() => serverApi.get<Customer>(`sales/customers/${quote.customer}`))
      : Promise.resolve(null),
    recordsById<Item>("items", quote.lines.map((line) => line.item)),
    isAccepted && roleHasPermission(role, PERMISSIONS.VIEW_ORDERS)
      ? tryServer(() => serverApi.list<SalesOrder>("sales/orders", { query: { customer: quote.customer, page_size: 200 } }))
      : Promise.resolve(null),
    isAccepted && roleHasPermission(role, PERMISSIONS.VIEW_INVOICES)
      ? tryServer(() => serverApi.list<Invoice>("sales/invoices", { query: { customer: quote.customer, page_size: 200 } }))
      : Promise.resolve(null),
    // The accounts on the most recent invoice are the best default for the
    // conversion: there is no organization-level default receivable account.
    isAccepted && canManage && roleHasPermission(role, PERMISSIONS.VIEW_INVOICES)
      ? tryServer(() => serverApi.list<Invoice>("sales/invoices", { query: { page_size: 1 } }))
      : Promise.resolve(null),
    // Converting to an invoice creates it with its warehouse, and a tracked
    // product line without one is refused (warehouse_required) — so the
    // organization's default warehouse backs up the remembered one.
    isAccepted && canManage && roleHasPermission(role, PERMISSIONS.VIEW_INVENTORY)
      ? tryServer(() => serverApi.list<Warehouse>("inventory/warehouses", { query: { page_size: 200, is_active: "true" } }))
      : Promise.resolve(null),
  ]);
  const defaultWarehouse = warehouses?.ok ? warehouses.data.results.find((row) => row.is_default) : undefined;

  const order = orders?.ok ? orders.data.results.find((row) => row.source_quote === quote.id) : undefined;
  const invoice = invoices?.ok ? invoices.data.results.find((row) => row.source_quote === quote.id) : undefined;
  const customerName = customer?.ok ? customer.data.display_name : null;
  const today = todayInZone(session.timeZone);
  const termsDays = customer?.ok ? customer.data.payment_terms_days : null;
  const previous = latestInvoice?.ok ? latestInvoice.data.results[0] : undefined;
  const expired = quote.status === "sent" && quote.expiry_date !== null && quote.expiry_date < today;

  return (
    <>
      <PageHeader
        title={quote.quote_number}
        breadcrumbs={[{ label: "Quotes", href: "/sales/quotes" }, { label: quote.quote_number }]}
        meta={
          <>
            <StatusBadge status={quote.status} map={QUOTE_STATUS} />
            {expired ? (
              <Badge tone="warning" size="sm">
                Past expiry date
              </Badge>
            ) : null}
          </>
        }
        description={customerName ? `Quoted to ${customerName}` : undefined}
        actions={
          <>
            <PrintButton />
            {canManage && quote.status === "draft" ? (
              <LinkButton href={`/sales/quotes/${quote.id}/edit`}>Edit</LinkButton>
            ) : null}
            {canManage && (quote.status === "draft" || quote.status === "sent") ? (
              <DocumentAction
                resource="sales/quotes"
                id={quote.id}
                action="cancel"
                label="Cancel quote"
                variant="danger"
                confirmTitle={`Cancel ${quote.quote_number}?`}
                confirmMessage={
                  <p>
                    The quote is withdrawn and can no longer be sent, accepted or converted. It stays on record as
                    cancelled. Nothing was recorded in the ledger, so nothing is reversed.
                  </p>
                }
                successTitle="Quote cancelled"
              />
            ) : null}
            {canManage && quote.status === "sent" ? (
              <DocumentAction
                resource="sales/quotes"
                id={quote.id}
                action="reject"
                label="Mark rejected"
                confirmTitle={`Mark ${quote.quote_number} rejected?`}
                confirmMessage={
                  <p>Records that the customer declined. A rejected quote cannot be accepted or converted later.</p>
                }
                successTitle="Quote marked rejected"
              />
            ) : null}
            {canManage && quote.status === "draft" ? (
              <DocumentAction
                resource="sales/quotes"
                id={quote.id}
                action="send"
                label="Mark sent"
                variant="primary"
                confirmTitle={`Mark ${quote.quote_number} sent?`}
                confirmMessage={
                  <>
                    <p>
                      Records that the quote has gone to the customer. It can no longer be edited. Nothing is posted to
                      the ledger.
                    </p>
                    <p className="mt-2">EasyBook does not email the quote; send it yourself, for example by printing it.</p>
                  </>
                }
                successTitle="Quote marked sent"
              />
            ) : null}
            {canManage && quote.status === "sent" ? (
              <DocumentAction
                resource="sales/quotes"
                id={quote.id}
                action="accept"
                label="Mark accepted"
                variant="primary"
                confirmTitle={`Mark ${quote.quote_number} accepted?`}
                confirmMessage={
                  <p>
                    Records the customer&apos;s acceptance. The quote can then be converted into a sales order and an
                    invoice. Nothing is posted.
                  </p>
                }
                successTitle="Quote marked accepted"
              />
            ) : null}
            {canManage && isAccepted ? (
              <QuoteConvertDialog
                quoteId={quote.id}
                quoteNumber={quote.quote_number}
                issueDate={formatDate(quote.issue_date)}
                converted={{ sales_order: Boolean(order), invoice: Boolean(invoice) }}
                defaults={{
                  today,
                  dueDate: (termsDays !== null ? addDays(today, termsDays) : null) ?? today,
                  receivableAccountId: previous?.receivable_account ?? null,
                  taxPayableAccountId: previous?.tax_payable_account ?? null,
                  warehouseId: previous?.warehouse ?? defaultWarehouse?.id ?? null,
                  rememberedFrom: previous?.invoice_number || null,
                  termsDays,
                }}
              />
            ) : null}
          </>
        }
      />

      <PageBody className="print-document">
        <div className="grid gap-4 lg:grid-cols-3">
          <PartyCard
            title="Customer"
            href={`/sales/customers/${quote.customer}`}
            name={customerName}
            code={customer?.ok ? customer.data.customer_code : null}
            gstin={customer?.ok ? customer.data.gstin : null}
            address={customer?.ok ? customer.data.billing_address : null}
          />
          <Card className="lg:col-span-2">
            <CardHeader title="Details" />
            <CardBody>
              <DetailList
                items={[
                  { label: "Quote date", value: formatDate(quote.issue_date) },
                  { label: "Expiry date", value: quote.expiry_date ? formatDate(quote.expiry_date) : "None" },
                  { label: "Currency", value: quote.currency },
                  ...(order
                    ? [
                        {
                          label: "Sales order",
                          value: (
                            <span className="flex items-center gap-2">
                              <Link href={`/sales/orders/${order.id}`} className="tabular text-brand-700 hover:underline">
                                {order.order_number}
                              </Link>
                              <StatusBadge status={order.status} map={SALES_ORDER_STATUS} size="sm" />
                            </span>
                          ),
                        },
                      ]
                    : []),
                  ...(invoice
                    ? [
                        {
                          label: "Invoice",
                          value: (
                            <span className="flex items-center gap-2">
                              <Link href={`/sales/invoices/${invoice.id}`} className="tabular text-brand-700 hover:underline">
                                {invoice.invoice_number || "Draft invoice"}
                              </Link>
                              <StatusBadge status={invoice.status} map={INVOICE_STATUS} size="sm" />
                            </span>
                          ),
                        },
                      ]
                    : []),
                ]}
              />
            </CardBody>
          </Card>
        </div>

        <Section title="Lines">
          <PricedLinesTable
            lines={quote.lines}
            items={items}
            currency={quote.currency}
            caption={`Lines of ${quote.quote_number}`}
            selfHref={`/sales/quotes/${quote.id}`}
          />
        </Section>

        <div className="grid gap-4 lg:grid-cols-5">
          <div className="flex flex-col gap-4 lg:col-span-3">
            {quote.notes || quote.terms ? (
              <Card>
                <CardHeader title="Notes and terms" />
                <CardBody>
                  <DetailList
                    columns={1}
                    items={[
                      ...(quote.notes ? [{ label: "Notes", value: <span className="whitespace-pre-line">{quote.notes}</span> }] : []),
                      ...(quote.terms ? [{ label: "Terms", value: <span className="whitespace-pre-line">{quote.terms}</span> }] : []),
                    ]}
                  />
                </CardBody>
              </Card>
            ) : null}
          </div>
          <div className="lg:col-span-2">
            <DocumentTotalsCard totals={quote} currency={quote.currency} footnote="Calculated by the accounting engine when the quote was saved." />
          </div>
        </div>
      </PageBody>
    </>
  );
}
