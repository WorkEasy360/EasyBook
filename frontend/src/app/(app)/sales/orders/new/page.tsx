import type { Metadata } from "next";
import { PageHeader } from "@/components/ui/page-header";
import { PageBody } from "@/components/ui/detail";
import { ForbiddenState } from "@/components/ui/states";
import { SalesOrderForm } from "@/features/sales/sales-order-form";
import { paramOf, type RawSearchParams } from "@/lib/list-query";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";

export const metadata: Metadata = { title: "New sales order" };

const CRUMBS = [{ label: "Sales orders", href: "/sales/orders" }, { label: "New" }];

/** `?customer=<id>` preselects the customer (from a customer page). */
export default async function NewSalesOrderPage({ searchParams }: { searchParams: Promise<RawSearchParams> }) {
  const session = await requireSession();
  const params = await searchParams;

  if (!roleHasPermission(session.role, PERMISSIONS.MANAGE_ORDERS)) {
    return (
      <>
        <PageHeader title="New sales order" breadcrumbs={CRUMBS} />
        <ForbiddenState resource="creating sales orders" />
      </>
    );
  }

  const customerId = paramOf(params, "customer");

  return (
    <>
      <PageHeader
        title="New sales order"
        breadcrumbs={CRUMBS}
        description="Saved as a draft with its order number. Confirm it to start delivering against it."
      />
      <PageBody className="max-w-6xl">
        <SalesOrderForm {...(customerId ? { initialCustomerId: customerId } : {})} />
      </PageBody>
    </>
  );
}
