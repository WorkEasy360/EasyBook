"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { Controller, useForm, useWatch } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Checkbox, FormError, FormField, Input, Textarea } from "@/components/ui/field";
import { useToast } from "@/components/ui/toast";
import { useOrg } from "@/components/providers/org-provider";
import { AccountPicker } from "@/features/shared/pickers";
import { useUnsavedChanges } from "@/lib/hooks/use-unsaved-changes";
import { useApiMutation } from "@/lib/hooks/use-api";
import { api } from "@/lib/api/browser";
import { formErrorOf, referenceOf } from "@/lib/api/errors";
import { serverFieldErrors } from "./form-errors";
import type { Vendor, VendorInput } from "@/types/api/purchases";

/**
 * Create / edit a vendor — the buy-side mirror of the customer form.
 *
 * Differences that come from the backend, not taste:
 *  - no credit limit: purchases.Vendor has none (a limit is something WE
 *    extend to a customer);
 *  - `default_payable_account` must be a LIABILITY account
 *    (services/vendors.py :: _validate_default_payable_account). It is only a
 *    default the bill form reads; the backend never applies it on its own;
 *  - payment terms of 0 mean "due on receipt".
 *
 * Client validation is for immediacy; VendorSerializer and the service are the
 * authority, and their errors are mapped back onto the inputs.
 */

const addressSchema = z.object({
  line1: z.string().max(255).optional(),
  line2: z.string().max(255).optional(),
  city: z.string().max(120).optional(),
  state: z.string().max(120).optional(),
  state_code: z.string().max(2).optional(),
  postal_code: z.string().max(20).optional(),
  country: z.string().max(120).optional(),
});

const schema = z.object({
  // purchases/models/vendor.py: vendor_code max_length=32, phone 32.
  vendor_code: z.string().trim().min(1, "A vendor code is required.").max(32, "At most 32 characters."),
  display_name: z.string().trim().min(1, "A display name is required.").max(255),
  legal_name: z.string().max(255),
  email: z.union([z.literal(""), z.email("Enter a valid email address.")]),
  phone: z.string().max(32, "At most 32 characters."),
  gstin: z.union([z.literal(""), z.string().length(15, "A GSTIN is 15 characters.")]),
  pan: z.union([z.literal(""), z.string().length(10, "A PAN is 10 characters.")]),
  payment_terms_days: z
    .number({ error: "Enter a number of days." })
    .int("Enter a whole number of days.")
    .min(0, "Payment terms cannot be negative."),
  default_payable_account: z.string().nullable(),
  notes: z.string().max(4000),
  is_active: z.boolean(),
  billing_address: addressSchema,
  shipping_address: addressSchema,
});

type VendorFormValues = z.infer<typeof schema>;

const FIELDS = Object.keys(schema.shape);

function toFormValues(vendor: Vendor | null): VendorFormValues {
  return {
    vendor_code: vendor?.vendor_code ?? "",
    display_name: vendor?.display_name ?? "",
    legal_name: vendor?.legal_name ?? "",
    email: vendor?.email ?? "",
    phone: vendor?.phone ?? "",
    gstin: vendor?.gstin ?? "",
    pan: vendor?.pan ?? "",
    payment_terms_days: vendor?.payment_terms_days ?? 30,
    default_payable_account: vendor?.default_payable_account ?? null,
    notes: vendor?.notes ?? "",
    is_active: vendor?.is_active ?? true,
    billing_address: vendor?.billing_address ?? {},
    shipping_address: vendor?.shipping_address ?? {},
  };
}

export function VendorForm({ vendor }: { vendor?: Vendor | null }) {
  const router = useRouter();
  const toast = useToast();
  const org = useOrg();
  const isEdit = Boolean(vendor);
  const [formError, setFormError] = React.useState<{ message: string | null; reference: string | null }>({
    message: null,
    reference: null,
  });

  const {
    control,
    register,
    handleSubmit,
    setError,
    setValue,
    formState: { errors, isDirty, isSubmitting },
  } = useForm<VendorFormValues>({
    resolver: zodResolver(schema),
    defaultValues: toFormValues(vendor ?? null),
  });

  useUnsavedChanges(isDirty && !isSubmitting);

  // Master data, not a financial document: a replayed create would be refused
  // by the unique vendor code, so no idempotency key is needed here.
  const mutation = useApiMutation<Vendor, VendorInput>(
    "purchases/vendors",
    (input) =>
      vendor ? api.patch<Vendor>(`purchases/vendors/${vendor.id}`, input) : api.post<Vendor>("purchases/vendors", input),
    {
      onSuccess: (saved) => {
        toast.push({
          tone: "success",
          title: isEdit ? "Vendor updated" : "Vendor created",
          description: saved.display_name,
        });
        router.push(`/purchases/vendors/${saved.id}`);
        router.refresh();
      },
      onError: (error) => {
        setFormError({ message: formErrorOf(error), reference: referenceOf(error) });
        const pairs = serverFieldErrors(error, FIELDS, {
          duplicate_vendor_code: "vendor_code",
          invalid_account_type: "default_payable_account",
          cross_org_reference: "default_payable_account",
        });
        for (const [field, message] of pairs) {
          setError(field as keyof VendorFormValues, { type: "server", message });
        }
      },
    },
  );

  function onSubmit(values: VendorFormValues) {
    setFormError({ message: null, reference: null });
    mutation.mutate({
      vendor_code: values.vendor_code.trim(),
      display_name: values.display_name.trim(),
      legal_name: values.legal_name,
      email: values.email,
      phone: values.phone,
      gstin: values.gstin.toUpperCase(),
      pan: values.pan.toUpperCase(),
      // Vendors are created in the organization's currency; the API has no
      // currency list to choose from in this build.
      currency: vendor?.currency ?? org.currency,
      payment_terms_days: values.payment_terms_days,
      default_payable_account: values.default_payable_account,
      notes: values.notes,
      is_active: values.is_active,
      billing_address: values.billing_address,
      shipping_address: values.shipping_address,
    });
  }

  const billing = useWatch({ control, name: "billing_address" });

  return (
    <form method="post" noValidate onSubmit={(event) => void handleSubmit(onSubmit)(event)} className="flex flex-col gap-4">
      <FormError message={formError.message} reference={formError.reference} />

      <Card>
        <CardHeader title="Details" />
        <CardBody className="grid gap-4 sm:grid-cols-2">
          <FormField
            label="Display name"
            required
            error={errors.display_name?.message ?? null}
            hint="How this vendor appears on bills and orders."
          >
            <Input autoFocus {...register("display_name")} />
          </FormField>

          <FormField
            label="Vendor code"
            required
            error={errors.vendor_code?.message ?? null}
            hint="Your internal reference. Must be unique."
          >
            <Input className="tabular" {...register("vendor_code")} />
          </FormField>

          <FormField label="Legal name" error={errors.legal_name?.message ?? null}>
            <Input {...register("legal_name")} />
          </FormField>

          <FormField label="Email" error={errors.email?.message ?? null}>
            <Input type="email" autoComplete="off" {...register("email")} />
          </FormField>

          <FormField label="Phone" error={errors.phone?.message ?? null}>
            <Input type="tel" autoComplete="off" {...register("phone")} />
          </FormField>

          <FormField
            label="GSTIN"
            error={errors.gstin?.message ?? null}
            hint="15 characters. Leave blank for an unregistered vendor."
          >
            <Input maxLength={15} className="tabular uppercase" {...register("gstin")} />
          </FormField>

          <FormField label="PAN" error={errors.pan?.message ?? null}>
            <Input maxLength={10} className="tabular uppercase" {...register("pan")} />
          </FormField>
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="Payables" description={`Amounts are in ${vendor?.currency ?? org.currency}.`} />
        <CardBody className="grid gap-4 sm:grid-cols-2">
          <FormField
            label="Payment terms (days)"
            required
            error={errors.payment_terms_days?.message ?? null}
            hint="Suggests a bill's due date. 0 means due on receipt."
          >
            <Input
              type="number"
              min={0}
              numeric
              // valueAsNumber: without it RHF hands the schema a string.
              {...register("payment_terms_days", { valueAsNumber: true })}
            />
          </FormField>

          <FormField
            label="Default payable account"
            error={errors.default_payable_account?.message ?? null}
            hint="A liability account. Suggested on new bills for this vendor; each bill still records its own."
          >
            <Controller
              control={control}
              name="default_payable_account"
              render={({ field }) => (
                <AccountPicker accountType="liability" value={field.value} onChange={field.onChange} />
              )}
            />
          </FormField>
        </CardBody>
      </Card>

      <Card>
        <CardHeader
          title="Billing address"
          actions={
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setValue("shipping_address", { ...billing }, { shouldDirty: true })}
            >
              Copy to shipping
            </Button>
          }
        />
        <CardBody>
          <AddressFields register={register} prefix="billing_address" />
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="Shipping address" />
        <CardBody>
          <AddressFields register={register} prefix="shipping_address" />
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="Other" />
        <CardBody className="flex flex-col gap-4">
          <FormField label="Notes" error={errors.notes?.message ?? null}>
            <Textarea rows={3} {...register("notes")} />
          </FormField>
          <Checkbox
            label="Active"
            hint="Inactive vendors stay on past documents but cannot be used on new ones."
            {...register("is_active")}
          />
        </CardBody>
      </Card>

      <div className="flex items-center justify-end gap-2">
        <Button variant="secondary" onClick={() => router.back()} disabled={mutation.isPending}>
          Cancel
        </Button>
        <Button type="submit" variant="primary" loading={mutation.isPending} loadingLabel={isEdit ? "Saving" : "Creating"}>
          {isEdit ? "Save changes" : "Create vendor"}
        </Button>
      </div>
    </form>
  );
}

function AddressFields({
  register,
  prefix,
}: {
  register: ReturnType<typeof useForm<VendorFormValues>>["register"];
  prefix: "billing_address" | "shipping_address";
}) {
  return (
    <div className="grid gap-4 sm:grid-cols-2">
      <FormField label="Address line 1" className="sm:col-span-2">
        <Input {...register(`${prefix}.line1`)} />
      </FormField>
      <FormField label="Address line 2" className="sm:col-span-2">
        <Input {...register(`${prefix}.line2`)} />
      </FormField>
      <FormField label="City">
        <Input {...register(`${prefix}.city`)} />
      </FormField>
      <FormField label="State">
        <Input {...register(`${prefix}.state`)} />
      </FormField>
      <FormField label="State code" hint="Two digits. Determines CGST/SGST versus IGST on bills.">
        <Input maxLength={2} className="tabular" {...register(`${prefix}.state_code`)} />
      </FormField>
      <FormField label="Postal code">
        <Input className="tabular" {...register(`${prefix}.postal_code`)} />
      </FormField>
      <FormField label="Country">
        <Input {...register(`${prefix}.country`)} />
      </FormField>
    </div>
  );
}
