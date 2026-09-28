import type { Metadata } from "next";
import { PageHeader } from "@/components/ui/page-header";
import { PageBody } from "@/components/ui/detail";
import { ErrorState, ForbiddenState } from "@/components/ui/states";
import { PaymentForm, type PaymentFormInitial } from "@/features/sales/payment-form";
import { serverApi, tryServer } from "@/lib/api/server";
import { paramOf, type RawSearchParams } from "@/lib/list-query";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { referenceOf } from "@/lib/api/errors";
import type { CustomerPayment, Invoice } from "@/types/api/sales";

export const metadata: Metadata = { title: "Record payment" };

const CRUMBS = [{ label: "Payments received", href: "/sales/payments" }, { label: "Record" }];

/**
 * `?customer=<id>` preselects the customer. `?invoice=<id>` (the invoice
 * page's "Record payment") also preselects that invoice's customer and
 * applies the invoice's server-reported balance due, which is where the
 * amount starts too.
 */
export default async function NewPaymentPage({ searchParams }: { searchParams: Promise<RawSearchParams> }) {
  const session = await requireSession();
  const params = await searchParams;
  const role = session.role;

  if (!roleHasPermission(role, PERMISSIONS.RECORD_PAYMENT)) {
    return (
      <>
        <PageHeader title="Record payment" breadcrumbs={CRUMBS} />
        <ForbiddenState resource="recording payments" />
      </>
    );
  }

  const invoiceId = paramOf(params, "invoice");

  const [invoice, latest] = await Promise.all([
    invoiceId && roleHasPermission(role, PERMISSIONS.VIEW_INVOICES)
      ? tryServer(() => serverApi.get<Invoice>(`sales/invoices/${invoiceId}`))
      : Promise.resolve(null),
    // The account the last payment was received into is the best default:
    // there is no organization-level default bank account.
    roleHasPermission(role, PERMISSIONS.VIEW_PAYMENTS)
      ? tryServer(() => serverApi.list<CustomerPayment>("sales/payments", { query: { page_size: 1 } }))
      : Promise.resolve(null),
  ]);

  if (invoice && !invoice.ok) {
    return (
      <>
        <PageHeader title="Record payment" breadcrumbs={CRUMBS} />
        <ErrorState
          title="Could not load the invoice being paid"
          message={invoice.error.message}
          reference={referenceOf(invoice.error)}
        />
      </>
    );
  }

  const previous = latest?.ok ? latest.data.results[0] : undefined;
  const initial: PaymentFormInitial = {
    destination_account_id: previous?.destination_account ?? null,
    rememberedFrom: previous?.payment_number ?? null,
  };

  const customerId = invoice?.ok ? invoice.data.customer : paramOf(params, "customer");
  if (customerId) initial.customer_id = customerId;

  // Only an open invoice takes an allocation (invoice_not_payable).
  const payable = invoice?.ok && (invoice.data.status === "sent" || invoice.data.status === "partially_paid");
  if (invoice?.ok && payable) {
    initial.amount = invoice.data.amount_due;
    initial.allocations = { [invoice.data.id]: invoice.data.amount_due };
  }

  return (
    <>
      <PageHeader
        title="Record payment"
        breadcrumbs={CRUMBS}
        description={
          invoice?.ok
            ? payable
              ? `Paying invoice ${invoice.data.invoice_number}. Adjust the amount if the customer paid part of it or more.`
              : `Invoice ${invoice.data.invoice_number || "(draft)"} has no balance to pay, so nothing is pre-applied.`
            : "Posted to the ledger as soon as it is recorded. A recorded payment cannot be edited."
        }
      />
      <PageBody className="max-w-6xl">
        <PaymentForm initial={initial} />
      </PageBody>
    </>
  );
}
