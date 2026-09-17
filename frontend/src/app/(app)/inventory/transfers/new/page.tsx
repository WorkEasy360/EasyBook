import type { Metadata } from "next";
import { PageHeader } from "@/components/ui/page-header";
import { PageBody } from "@/components/ui/detail";
import { ForbiddenState } from "@/components/ui/states";
import { TransferForm } from "@/features/inventory/transfer-form";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { paramOf, type RawSearchParams } from "@/lib/list-query";

export const metadata: Metadata = { title: "Transfer stock" };

const CRUMBS = [{ label: "Inventory", href: "/inventory" }, { label: "Transfer" }];

export default async function NewTransferPage({ searchParams }: { searchParams: Promise<RawSearchParams> }) {
  const session = await requireSession();
  const params = await searchParams;

  if (!roleHasPermission(session.role, PERMISSIONS.TRANSFER_INVENTORY)) {
    return (
      <>
        <PageHeader title="Transfer stock" breadcrumbs={CRUMBS} />
        <ForbiddenState resource="transferring stock" />
      </>
    );
  }

  const itemId = paramOf(params, "item");

  return (
    <>
      <PageHeader
        title="Transfer stock"
        breadcrumbs={CRUMBS}
        description="Transfers take effect immediately. There is no transfer history list in the API — find past transfers in stock movements."
      />
      <PageBody className="max-w-3xl">
        <TransferForm {...(itemId ? { initialItemId: itemId } : {})} />
      </PageBody>
    </>
  );
}
