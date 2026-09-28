import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { PageHeader } from "@/components/ui/page-header";
import { PageBody } from "@/components/ui/detail";
import { ErrorState, ForbiddenState } from "@/components/ui/states";
import { VendorForm } from "@/features/purchases/vendor-form";
import { serverApi, tryServer } from "@/lib/api/server";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { ApiError, referenceOf } from "@/lib/api/errors";
import type { Vendor } from "@/types/api/purchases";

export const metadata: Metadata = { title: "Edit vendor" };

export default async function EditVendorPage({ params }: { params: Promise<{ id: string }> }) {
  const session = await requireSession();
  const { id } = await params;

  if (!roleHasPermission(session.role, PERMISSIONS.MANAGE_VENDORS)) {
    return (
      <>
        <PageHeader title="Edit vendor" />
        <ForbiddenState resource="editing vendors" />
      </>
    );
  }

  const result = await tryServer(() => serverApi.get<Vendor>(`purchases/vendors/${id}`));
  if (!result.ok) {
    if (result.error instanceof ApiError && result.error.isNotFound) notFound();
    return (
      <>
        <PageHeader title="Edit vendor" />
        <ErrorState message={result.error.message} reference={referenceOf(result.error)} />
      </>
    );
  }

  const vendor = result.data;

  return (
    <>
      <PageHeader
        title={`Edit ${vendor.display_name}`}
        breadcrumbs={[
          { label: "Vendors", href: "/purchases/vendors" },
          { label: vendor.display_name, href: `/purchases/vendors/${vendor.id}` },
          { label: "Edit" },
        ]}
      />
      <PageBody className="max-w-3xl">
        <VendorForm vendor={vendor} />
      </PageBody>
    </>
  );
}
