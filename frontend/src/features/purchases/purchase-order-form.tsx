"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { Controller, FormProvider, useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { FormError, FormField, Input, Textarea } from "@/components/ui/field";
import { useToast } from "@/components/ui/toast";
import { useOrg } from "@/components/providers/org-provider";
import { VendorPicker, WarehousePicker } from "@/features/shared/pickers";
import {
  EMPTY_PRICED_LINE,
  EstimatedTotals,
  PricedLinesEditor,
  fromPricedLine,
  toPricedLineInput,
} from "@/features/documents/priced-lines-editor";
import { useUnsavedChanges } from "@/lib/hooks/use-unsaved-changes";
import { useIdempotentMutation } from "@/lib/hooks/use-api";
import { api } from "@/lib/api/browser";
import { formErrorOf, referenceOf } from "@/lib/api/errors";
import { todayInZone } from "@/lib/datetime";
import { serverFieldErrors } from "./form-errors";
import { purchaseLineSchema } from "./line-schema";
import type { PurchaseOrder, PurchaseOrderInput, PurchaseOrderUpdateInput } from "@/types/api/purchases";

/**
 * Create or edit a DRAFT purchase order.
 *
 * A purchase order posts nothing — no journal, no stock. It is the agreed
 * quantity and price that goods receipts and bills are later matched against.
 * Its number is allocated when the draft is created.
 *
 * Once saved, PurchaseOrderDetailView.update accepts only the dates,
 * reference, notes, terms and lines; the vendor and warehouse are shown
 * read-only rather than offered and then ignored. Approval is a separate,
 * confirmed action on the order page.
 */

const DATE = /^\d{4}-\d{2}-\d{2}$/;

const schema = z
  .object({
    vendor_id: z.string().min(1, "Choose a vendor."),
    order_date: z.string().regex(DATE, "Enter the order date."),
    expected_date: z.union([z.literal(""), z.string().regex(DATE, "Enter a valid date.")]),
    reference: z.string().max(255, "At most 255 characters."),
    warehouse_id: z.string().nullable(),
    notes: z.string().max(4000),
    terms: z.string().max(4000),
    lines: z.array(purchaseLineSchema).min(1, "Add at least one line."),
  })
  // services/purchase_orders.py :: purchase_order_expected_date_invalid.
  .refine((values) => values.expected_date === "" || values.expected_date >= values.order_date, {
    message: "The expected date cannot be before the order date.",
    path: ["expected_date"],
  });

export type PurchaseOrderFormValues = z.infer<typeof schema>;
export type PurchaseOrderFormInitial = Partial<PurchaseOrderFormValues>;

const FIELDS = ["vendor_id", "order_date", "expected_date", "reference", "warehouse_id", "notes", "terms"] as const;

function toValues(order: PurchaseOrder | undefined, initial: PurchaseOrderFormInitial, today: string): PurchaseOrderFormValues {
  if (order) {
    return {
      vendor_id: order.vendor,
      order_date: order.order_date,
      expected_date: order.expected_date ?? "",
      reference: order.reference,
      warehouse_id: order.warehouse,
      notes: order.notes,
      terms: order.terms,
      lines: order.lines.map(fromPricedLine),
    };
  }
  return {
    vendor_id: initial.vendor_id ?? "",
    order_date: initial.order_date ?? today,
    expected_date: initial.expected_date ?? "",
    reference: initial.reference ?? "",
    warehouse_id: initial.warehouse_id ?? null,
    notes: initial.notes ?? "",
    terms: initial.terms ?? "",
    lines: initial.lines?.length ? initial.lines : [{ ...EMPTY_PRICED_LINE }],
  };
}

export function PurchaseOrderForm({
  order,
  initial = {},
  rememberedFrom,
}: {
  order?: PurchaseOrder;
  initial?: PurchaseOrderFormInitial;
  /** Order number the default warehouse was taken from, for the hint. */
  rememberedFrom?: string | null;
}) {
  const router = useRouter();
  const toast = useToast();
  const org = useOrg();
  const isEdit = Boolean(order);
  const [formError, setFormError] = React.useState<{ message: string | null; reference: string | null }>({
    message: null,
    reference: null,
  });

  const form = useForm<PurchaseOrderFormValues>({
    resolver: zodResolver(schema),
    defaultValues: toValues(order, initial, todayInZone(org.timeZone)),
  });
  const {
    control,
    register,
    handleSubmit,
    setError,
    formState: { errors, isDirty, isSubmitting },
  } = form;

  useUnsavedChanges(isDirty && !isSubmitting);

  const mutation = useIdempotentMutation<PurchaseOrder, PurchaseOrderInput | PurchaseOrderUpdateInput>(
    "purchases/orders",
    (input, idempotencyKey) =>
      order
        ? api.patch<PurchaseOrder>(`purchases/orders/${order.id}`, input)
        : api.post<PurchaseOrder>("purchases/orders", input, { idempotencyKey }),
    {
      onSuccess: (saved) => {
        toast.push({ tone: "success", title: isEdit ? "Draft purchase order saved" : "Draft purchase order created" });
        router.push(`/purchases/orders/${saved.id}`);
        router.refresh();
      },
      onError: (error) => {
        setFormError({ message: formErrorOf(error), reference: referenceOf(error) });
        const pairs = serverFieldErrors(error, FIELDS, {
          purchase_order_expected_date_invalid: "expected_date",
          vendor_inactive: "vendor_id",
        });
        for (const [field, message] of pairs) {
          setError(field as (typeof FIELDS)[number], { type: "server", message });
        }
      },
    },
  );

  function onSubmit(values: PurchaseOrderFormValues) {
    setFormError({ message: null, reference: null });
    const lines = values.lines.map(toPricedLineInput);
    const expected = values.expected_date === "" ? null : values.expected_date;

    if (order) {
      mutation.mutate({
        order_date: values.order_date,
        expected_date: expected,
        reference: values.reference,
        notes: values.notes,
        terms: values.terms,
        lines,
      });
      return;
    }
    mutation.mutate({
      vendor_id: values.vendor_id,
      order_date: values.order_date,
      expected_date: expected,
      reference: values.reference,
      warehouse_id: values.warehouse_id,
      notes: values.notes,
      terms: values.terms,
      lines,
    });
  }

  const lockedHint = isEdit ? "Fixed once the draft is saved." : undefined;

  return (
    <FormProvider {...form}>
      <form method="post" noValidate onSubmit={(event) => void handleSubmit(onSubmit)(event)} className="flex flex-col gap-4">
        <FormError message={formError.message} reference={formError.reference} />

        <Card>
          <CardHeader title="Purchase order" />
          <CardBody className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            <FormField
              label="Vendor"
              required
              error={errors.vendor_id?.message ?? null}
              className="sm:col-span-2 lg:col-span-1"
              {...(lockedHint ? { hint: lockedHint } : {})}
            >
              <Controller
                control={control}
                name="vendor_id"
                render={({ field }) => (
                  <VendorPicker value={field.value || null} onChange={(value) => field.onChange(value ?? "")} disabled={isEdit} />
                )}
              />
            </FormField>

            <FormField label="Order date" required error={errors.order_date?.message ?? null}>
              <Input type="date" {...register("order_date")} />
            </FormField>

            <FormField label="Expected date" error={errors.expected_date?.message ?? null} hint="When you expect the goods.">
              <Input type="date" {...register("expected_date")} />
            </FormField>

            <FormField label="Reference" error={errors.reference?.message ?? null} hint="A quote number or your own reference.">
              <Input {...register("reference")} />
            </FormField>

            <FormField
              label="Deliver to warehouse"
              error={errors.warehouse_id?.message ?? null}
              hint={
                lockedHint ??
                (rememberedFrom ? `Taken from order ${rememberedFrom}.` : "Where the goods are expected. Each receipt still names its own.")
              }
            >
              <Controller
                control={control}
                name="warehouse_id"
                render={({ field }) => <WarehousePicker value={field.value} onChange={field.onChange} disabled={isEdit} />}
              />
            </FormField>
          </CardBody>
        </Card>

        <PricedLinesEditor
          usage="purchase"
          description="Agreed quantities and prices. Goods receipts and bills are matched against these."
        />

        <div className="grid gap-4 lg:grid-cols-5">
          <Card className="lg:col-span-3">
            <CardHeader title="Notes and terms" />
            <CardBody className="flex flex-col gap-4">
              <FormField label="Notes" error={errors.notes?.message ?? null}>
                <Textarea rows={3} {...register("notes")} />
              </FormField>
              <FormField label="Terms" error={errors.terms?.message ?? null}>
                <Textarea rows={3} {...register("terms")} />
              </FormField>
            </CardBody>
          </Card>
          <div className="lg:col-span-2">
            <EstimatedTotals currency={org.currency} />
          </div>
        </div>

        <div className="flex items-center justify-end gap-2">
          <Button variant="secondary" onClick={() => router.back()} disabled={mutation.isPending}>
            Cancel
          </Button>
          <Button type="submit" variant="primary" loading={mutation.isPending} loadingLabel="Saving">
            {isEdit ? "Save draft" : "Save as draft"}
          </Button>
        </div>
      </form>
    </FormProvider>
  );
}
