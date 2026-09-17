"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { Controller, useForm, useWatch } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Checkbox, FormError, FormField, Input, Textarea } from "@/components/ui/field";
import { NumericInput } from "@/components/ui/numeric-input";
import { useToast } from "@/components/ui/toast";
import { useOrg } from "@/components/providers/org-provider";
import { useUnsavedChanges } from "@/lib/hooks/use-unsaved-changes";
import { useApiMutation } from "@/lib/hooks/use-api";
import { api } from "@/lib/api/browser";
import { fieldErrorsOf, formErrorOf, referenceOf } from "@/lib/api/errors";
import type { Customer, CustomerInput } from "@/types/api/sales";

/**
 * Create / edit a customer.
 *
 * Client-side validation is for immediacy only — the backend's serializer is
 * the authority (spec §19). When it rejects a submission, the errors are
 * mapped back onto the matching inputs so the user is not left hunting.
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
  customer_code: z.string().min(1, "A customer code is required.").max(50),
  display_name: z.string().min(1, "A display name is required.").max(255),
  legal_name: z.string().max(255).optional(),
  email: z.union([z.literal(""), z.email("Enter a valid email address.")]),
  phone: z.string().max(40).optional(),
  // 15 characters: 2 state + 10 PAN + 1 entity + 1 'Z' + 1 checksum. The
  // backend validates the real format; this only catches a typo early.
  gstin: z.union([z.literal(""), z.string().length(15, "A GSTIN is 15 characters.")]),
  pan: z.union([z.literal(""), z.string().length(10, "A PAN is 10 characters.")]),
  payment_terms_days: z
    .number({ error: "Enter a number of days." })
    .int("Enter a whole number of days.")
    .min(0)
    .max(365, "Payment terms cannot exceed 365 days."),
  credit_limit: z.string(),
  notes: z.string().max(2000).optional(),
  is_active: z.boolean(),
  billing_address: addressSchema,
  shipping_address: addressSchema,
});

type CustomerFormValues = z.infer<typeof schema>;

function toFormValues(customer: Customer | null): CustomerFormValues {
  return {
    customer_code: customer?.customer_code ?? "",
    display_name: customer?.display_name ?? "",
    legal_name: customer?.legal_name ?? "",
    email: customer?.email ?? "",
    phone: customer?.phone ?? "",
    gstin: customer?.gstin ?? "",
    pan: customer?.pan ?? "",
    payment_terms_days: customer?.payment_terms_days ?? 30,
    credit_limit: customer?.credit_limit ?? "",
    notes: customer?.notes ?? "",
    is_active: customer?.is_active ?? true,
    billing_address: customer?.billing_address ?? {},
    shipping_address: customer?.shipping_address ?? {},
  };
}

export function CustomerForm({ customer }: { customer?: Customer | null }) {
  const router = useRouter();
  const toast = useToast();
  const org = useOrg();
  const isEdit = Boolean(customer);

  const [formError, setFormError] = React.useState<string | null>(null);
  const [reference, setReference] = React.useState<string | null>(null);

  const form = useForm<CustomerFormValues>({
    resolver: zodResolver(schema),
    defaultValues: toFormValues(customer ?? null),
  });

  const {
    control,
    register,
    handleSubmit,
    setError,
    setValue,
    formState: { errors, isDirty, isSubmitting },
  } = form;

  useUnsavedChanges(isDirty && !isSubmitting);

  const mutation = useApiMutation<Customer, CustomerInput>(
    "sales/customers",
    (input) =>
      isEdit && customer
        ? api.patch<Customer>(`sales/customers/${customer.id}`, input)
        : api.post<Customer>("sales/customers", input),
    {
      onSuccess: (saved) => {
        toast.push({
          tone: "success",
          title: isEdit ? "Customer updated" : "Customer created",
          description: saved.display_name,
        });
        router.push(`/sales/customers/${saved.id}`);
        // The list is a Server Component; refresh re-runs it so the new row
        // appears rather than showing a cached page without it.
        router.refresh();
      },
      onError: (error) => {
        setFormError(formErrorOf(error));
        setReference(referenceOf(error));

        // Attach server validation to the matching inputs.
        for (const [field, messages] of Object.entries(fieldErrorsOf(error))) {
          if (field in schema.shape && messages[0]) {
            setError(field as keyof CustomerFormValues, { type: "server", message: messages[0] });
          }
        }
      },
    },
  );

  function onSubmit(values: CustomerFormValues) {
    setFormError(null);
    setReference(null);

    const payload: CustomerInput = {
      customer_code: values.customer_code,
      display_name: values.display_name,
      legal_name: values.legal_name ?? "",
      email: values.email,
      phone: values.phone ?? "",
      gstin: values.gstin,
      pan: values.pan,
      currency: org.currency,
      payment_terms_days: values.payment_terms_days,
      // "" means "no limit", which the API expects as null rather than "".
      credit_limit: values.credit_limit === "" ? null : values.credit_limit,
      notes: values.notes ?? "",
      is_active: values.is_active,
      billing_address: values.billing_address,
      shipping_address: values.shipping_address,
    };

    mutation.mutate(payload);
  }

  const billing = useWatch({ control, name: "billing_address" });

  function copyBillingToShipping() {
    setValue("shipping_address", { ...billing }, { shouldDirty: true });
  }

  return (
    <form
      method="post"
      onSubmit={(event) => void handleSubmit(onSubmit)(event)}
      className="flex flex-col gap-4"
      noValidate
    >
      <FormError message={formError} reference={reference} />

      <Card>
        <CardHeader title="Details" />
        <CardBody className="grid gap-4 sm:grid-cols-2">
          <FormField
            label="Display name"
            required
            error={errors.display_name?.message ?? null}
            hint="How this customer appears on documents."
          >
            <Input autoFocus {...register("display_name")} />
          </FormField>

          <FormField
            label="Customer code"
            required
            error={errors.customer_code?.message ?? null}
            hint="Your internal reference. Must be unique."
          >
            <Input {...register("customer_code")} />
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
            hint="15 characters. Leave blank for an unregistered customer."
          >
            <Input maxLength={15} className="tabular uppercase" {...register("gstin")} />
          </FormField>

          <FormField label="PAN" error={errors.pan?.message ?? null}>
            <Input maxLength={10} className="tabular uppercase" {...register("pan")} />
          </FormField>
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="Terms" description={`Amounts are in ${org.currency}.`} />
        <CardBody className="grid gap-4 sm:grid-cols-2">
          <FormField
            label="Payment terms (days)"
            required
            error={errors.payment_terms_days?.message ?? null}
            hint="Used to calculate an invoice due date."
          >
            <Input
              type="number"
              min={0}
              max={365}
              numeric
              // valueAsNumber: without it RHF hands the schema a string
              // and the number validator rejects every value.
              {...register("payment_terms_days", { valueAsNumber: true })}
            />
          </FormField>

          <FormField
            label="Credit limit"
            error={errors.credit_limit?.message ?? null}
            hint="Leave blank for no limit."
          >
            <Controller
              control={control}
              name="credit_limit"
              render={({ field }) => (
                <NumericInput value={field.value} onValueChange={field.onChange} onBlur={field.onBlur} />
              )}
            />
          </FormField>
        </CardBody>
      </Card>

      <Card>
        <CardHeader
          title="Billing address"
          actions={
            <Button variant="ghost" size="sm" onClick={copyBillingToShipping}>
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
            hint="Inactive customers stay on past documents but cannot be selected for new ones."
            {...register("is_active")}
          />
        </CardBody>
      </Card>

      <div className="flex items-center justify-end gap-2">
        <Button variant="secondary" onClick={() => router.back()} disabled={mutation.isPending}>
          Cancel
        </Button>
        <Button
          type="submit"
          variant="primary"
          loading={mutation.isPending}
          loadingLabel={isEdit ? "Saving" : "Creating"}
        >
          {isEdit ? "Save changes" : "Create customer"}
        </Button>
      </div>
    </form>
  );
}

type AddressPrefix = "billing_address" | "shipping_address";

function AddressFields({
  register,
  prefix,
}: {
  register: ReturnType<typeof useForm<CustomerFormValues>>["register"];
  prefix: AddressPrefix;
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
      <FormField
        label="State code"
        hint="Two digits. Determines CGST/SGST versus IGST."
      >
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
