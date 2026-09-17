import type { Metadata } from "next";
import { PageHeader } from "@/components/ui/page-header";
import { PageBody } from "@/components/ui/detail";
import { EmptyState, ErrorState, ForbiddenState } from "@/components/ui/states";
import { VendorCreditForm, type VendorCreditFormInitial } from "@/features/purchases/vendor-credit-form";
import { serverApi, tryServer } from "@/lib/api/server";
import { paramOf, type RawSearchParams } from "@/lib/list-query";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { referenceOf } from "@/lib/api/errors";
import type { Bill } from "@/types/api/purchases";

export const metadata: Metadata = { title: "New vendor credit" };

const CRUMBS = [{ label: "Vendor credits", href: "/purchases/vendor-credits" }, { label: "New" }];

/**
 * `?vendor=<id>` preselects the vendor. `?bill=<id>` credits a posted bill:
 * the vendor and warehouse come from the bill and each line arrives linked
 * through `source_bill_line_id` (which caps the credited quantity at what was
 * billed) with the bill's price, tax and expense account. Remove or reduce
 * the lines that are not being credited.
 */
export default async function NewVendorCreditPage({ searchParams }: { searchParams: Promise<RawSearchParams> }) {
  const session = await requireSession();
  const params = await searchParams;

  if (!roleHasPermission(session.role, PERMISSIONS.ISSUE_VENDOR_CREDIT)) {
    return (
      <>
        <PageHeader title="New vendor credit" breadcrumbs={CRUMBS} />
        <ForbiddenState resource="creating vendor credits" />
      </>
    );
  }

  const billId = paramOf(params, "bill");
  const initial: VendorCreditFormInitial = {};
  const vendorId = paramOf(params, "vendor");
  if (vendorId) initial.vendor_id = vendorId;
  let description = "Saved as a draft. Nothing is posted until the credit is issued.";

  if (billId) {
    const result = await tryServer(() => serverApi.get<Bill>(`purchases/bills/${billId}`));
    if (!result.ok) {
      return (
        <>
          <PageHeader title="New vendor credit" breadcrumbs={CRUMBS} />
          <ErrorState title="Could not load the bill to credit" message={result.error.message} reference={referenceOf(result.error)} />
        </>
      );
    }
    const bill = result.data;
    if (bill.status === "draft" || bill.status === "void") {
      return (
        <>
          <PageHeader title="New vendor credit" breadcrumbs={CRUMBS} />
          <EmptyState
            title={bill.status === "draft" ? "This bill is still a draft" : "This bill is void"}
            description="A vendor credit corrects a posted bill. Edit a draft bill instead."
            action={{ label: "Back to the bill", href: `/purchases/bills/${bill.id}` }}
          />
        </>
      );
    }
    initial.vendor_id = bill.vendor;
    initial.source_bill_id = bill.id;
    initial.bill_number = bill.bill_number;
    initial.reference = bill.vendor_bill_number || bill.bill_number;
    if (bill.warehouse) initial.warehouse_id = bill.warehouse;
    initial.lines = bill.lines.map((line) => ({
      item_id: line.item,
      description: line.description,
      quantity: line.quantity,
      unit_price: line.unit_price,
      discount_percent: line.discount_percent,
      tax_rate: line.tax_rate,
      expense_account_id: line.expense_account,
      source_bill_line_id: line.id,
      return_stock: false,
      unit_cost: "",
    }));
    description = `Crediting ${bill.bill_number}. Every line starts at the billed quantity and price — reduce them to what the vendor is crediting.`;
  }

  return (
    <>
      <PageHeader title="New vendor credit" breadcrumbs={CRUMBS} description={description} />
      <PageBody className="max-w-6xl">
        <VendorCreditForm initial={initial} />
      </PageBody>
    </>
  );
}
