import type { Metadata } from "next";
import { PageHeader } from "@/components/ui/page-header";
import { PageBody } from "@/components/ui/detail";
import { ErrorState, ForbiddenState } from "@/components/ui/states";
import { CreditNoteForm, type CreditNoteFormInitial } from "@/features/sales/credit-note-form";
import { trimQuantity } from "@/features/sales/fulfillment";
import { serverApi, tryServer } from "@/lib/api/server";
import { paramOf, type RawSearchParams } from "@/lib/list-query";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { referenceOf } from "@/lib/api/errors";
import type { Invoice } from "@/types/api/sales";
import { fromPricedLine } from "@/features/documents/priced-lines";

export const metadata: Metadata = { title: "New credit note" };

const CRUMBS = [{ label: "Credit notes", href: "/sales/credit-notes" }, { label: "New" }];

/**
 * `?invoice=<id>` credits an invoice: the customer, receivable and tax
 * accounts and warehouse are the invoice's, and its lines arrive linked
 * through `source_invoice_line_id` (which caps each at the invoiced quantity,
 * credit_quantity_exceeds_invoice). The credit note inherits the invoice's
 * tax treatment on the server.
 *
 * `?customer=<id>` preselects the customer for a credit note with no invoice.
 */
export default async function NewCreditNotePage({ searchParams }: { searchParams: Promise<RawSearchParams> }) {
  const session = await requireSession();
  const params = await searchParams;
  const role = session.role;

  if (!roleHasPermission(role, PERMISSIONS.ISSUE_CREDIT_NOTE)) {
    return (
      <>
        <PageHeader title="New credit note" breadcrumbs={CRUMBS} />
        <ForbiddenState resource="creating credit notes" />
      </>
    );
  }

  const invoiceId = paramOf(params, "invoice");
  const invoice =
    invoiceId && roleHasPermission(role, PERMISSIONS.VIEW_INVOICES)
      ? await tryServer(() => serverApi.get<Invoice>(`sales/invoices/${invoiceId}`))
      : null;

  if (invoice && !invoice.ok) {
    return (
      <>
        <PageHeader title="New credit note" breadcrumbs={CRUMBS} />
        <ErrorState
          title="Could not load the invoice to credit"
          message={invoice.error.message}
          reference={referenceOf(invoice.error)}
        />
      </>
    );
  }

  const initial: CreditNoteFormInitial = {};
  const customerParam = paramOf(params, "customer");
  if (customerParam) initial.customer_id = customerParam;

  if (invoice?.ok) {
    const source = invoice.data;
    initial.customer_id = source.customer;
    initial.source_invoice_id = source.id;
    initial.receivable_account_id = source.receivable_account;
    initial.tax_payable_account_id = source.tax_payable_account;
    initial.warehouse_id = source.warehouse;
    initial.lines = source.lines.map((line) => ({
      ...fromPricedLine(line),
      quantity: trimQuantity(line.quantity),
      restock: false,
      unit_cost: "",
      source_invoice_line_id: line.id,
    }));
  }

  const posted = invoice?.ok && invoice.data.status !== "draft" && invoice.data.status !== "void";

  return (
    <>
      <PageHeader
        title="New credit note"
        breadcrumbs={
          invoice?.ok
            ? [
                { label: "Invoices", href: "/sales/invoices" },
                { label: invoice.data.invoice_number || "Draft invoice", href: `/sales/invoices/${invoice.data.id}` },
                { label: "New credit note" },
              ]
            : CRUMBS
        }
        description={
          invoice?.ok
            ? posted
              ? `Crediting invoice ${invoice.data.invoice_number}. Remove or reduce lines to credit only part of it.`
              : `Invoice ${invoice.data.invoice_number || "(draft)"} is ${invoice.data.status}; a credit note is normally raised against a posted invoice.`
            : "Saved as a draft. Nothing is recorded in the ledger until the credit note is issued."
        }
      />
      <PageBody className="max-w-6xl">
        <CreditNoteForm
          initial={initial}
          sourceInvoice={
            invoice?.ok
              ? { number: invoice.data.invoice_number || "draft", amountDue: invoice.data.amount_due, currency: invoice.data.currency }
              : null
          }
        />
      </PageBody>
    </>
  );
}
