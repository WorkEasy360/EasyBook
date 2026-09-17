import type { Metadata } from "next";
import { PageHeader } from "@/components/ui/page-header";
import { PageBody } from "@/components/ui/detail";
import { ErrorState, ForbiddenState } from "@/components/ui/states";
import { VendorPaymentForm, type VendorPaymentFormInitial } from "@/features/purchases/vendor-payment-form";
import { serverApi, tryServer } from "@/lib/api/server";
import { paramOf, type RawSearchParams } from "@/lib/list-query";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { referenceOf } from "@/lib/api/errors";
import type { Bill } from "@/types/api/purchases";

export const metadata: Metadata = { title: "Record vendor payment" };

const CRUMBS = [{ label: "Payments made", href: "/purchases/payments" }, { label: "New" }];

/**
 * `?vendor=<id>` preselects the vendor; `&bill=<id>` also pre-allocates the
 * bill's current balance due (the server's `amount_due`, read here, never
 * computed). A bill that can no longer take a payment is not pre-allocated.
 */
export default async function NewVendorPaymentPage({ searchParams }: { searchParams: Promise<RawSearchParams> }) {
  const session = await requireSession();
  const params = await searchParams;

  if (!roleHasPermission(session.role, PERMISSIONS.RECORD_VENDOR_PAYMENT)) {
    return (
      <>
        <PageHeader title="Record vendor payment" breadcrumbs={CRUMBS} />
        <ForbiddenState resource="recording vendor payments" />
      </>
    );
  }

  const billId = paramOf(params, "bill");
  const initial: VendorPaymentFormInitial = {};
  const vendorId = paramOf(params, "vendor");
  if (vendorId) initial.vendor_id = vendorId;

  let description = "Posted to the ledger as soon as it is recorded. Payments cannot be edited afterwards.";

  if (billId) {
    const bill = await tryServer(() => serverApi.get<Bill>(`purchases/bills/${billId}`));
    if (!bill.ok) {
      return (
        <>
          <PageHeader title="Record vendor payment" breadcrumbs={CRUMBS} />
          <ErrorState title="Could not load the bill to pay" message={bill.error.message} reference={referenceOf(bill.error)} />
        </>
      );
    }
    initial.vendor_id = bill.data.vendor;
    // services/payments.py :: bill_not_payable.
    if (bill.data.status === "open" || bill.data.status === "partially_paid") {
      initial.bill = { id: bill.data.id, amount_due: bill.data.amount_due };
      description = `Paying ${bill.data.bill_number || "the bill"}. Its balance due is applied in full; adjust it to pay part.`;
    } else {
      description = `${bill.data.bill_number || "That bill"} is not open for payment, so nothing is pre-applied.`;
    }
  }

  return (
    <>
      <PageHeader title="Record vendor payment" breadcrumbs={CRUMBS} description={description} />
      <PageBody className="max-w-6xl">
        <VendorPaymentForm initial={initial} />
      </PageBody>
    </>
  );
}
