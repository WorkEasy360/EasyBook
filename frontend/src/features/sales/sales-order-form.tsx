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
import { CustomerPicker } from "@/features/shared/pickers";
import {
  EMPTY_PRICED_LINE,
  EstimatedTotals,
  PricedLinesEditor,
  fromPricedLine,
  pricedLineSchema,
  toPricedLineInput,
} from "@/features/documents/priced-lines-editor";
import { useUnsavedChanges } from "@/lib/hooks/use-unsaved-changes";
import { useIdempotentMutation } from "@/lib/hooks/use-api";
import { api } from "@/lib/api/browser";
import { fieldErrorsOf, formErrorOf, referenceOf } from "@/lib/api/errors";
import { todayInZone } from "@/lib/datetime";
import type { SalesOrder, SalesOrderInput, SalesOrderUpdateInput } from "@/types/api/sales";

/**
 * Create or edit a DRAFT sales order.
 *
 * An order is a commitment, not a financial record: confirming it posts
 * nothing and moves no stock (sales/services/sales_orders.py). Stock leaves
 * only when a delivery challan against it is dispatched. After creation the
 * backend accepts only the order date, notes, terms and lines
 * (SalesOrderDetailView.update).
 */

const schema = z.object({
  customer_id: z.string().min(1, "Choose a customer."),
  order_date: z.string().regex(/^\d{4}-\d{2}-\d{2}$/, "Enter the order date."),
  notes: z.string().max(4000),
  terms: z.string().max(4000),
  lines: z.array(pricedLineSchema).min(1, "Add at least one line."),
});

type Values = z.infer<typeof schema>;

export function SalesOrderForm({ order, initialCustomerId }: { order?: SalesOrder; initialCustomerId?: string }) {
  const router = useRouter();
  const toast = useToast();
  const org = useOrg();
  const isEdit = Boolean(order);
  const [formError, setFormError] = React.useState<{ message: string | null; reference: string | null }>({
    message: null,
    reference: null,
  });

  const form = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: order
      ? {
          customer_id: order.customer,
          order_date: order.order_date,
          notes: order.notes,
          terms: order.terms,
          lines: order.lines.map(fromPricedLine),
        }
      : {
          customer_id: initialCustomerId ?? "",
          order_date: todayInZone(org.timeZone),
          notes: "",
          terms: "",
          lines: [{ ...EMPTY_PRICED_LINE }],
        },
  });
  const {
    control,
    register,
    handleSubmit,
    setError,
    formState: { errors, isDirty, isSubmitting },
  } = form;

  useUnsavedChanges(isDirty && !isSubmitting);

  const mutation = useIdempotentMutation<SalesOrder, SalesOrderInput | SalesOrderUpdateInput>(
    "sales/orders",
    (input, idempotencyKey) =>
      order
        ? api.patch<SalesOrder>(`sales/orders/${order.id}`, input)
        : api.post<SalesOrder>("sales/orders", input, { idempotencyKey }),
    {
      onSuccess: (saved) => {
        toast.push({ tone: "success", title: isEdit ? "Draft order saved" : "Draft order created" });
        router.push(`/sales/orders/${saved.id}`);
        router.refresh();
      },
      onError: (error) => {
        setFormError({ message: formErrorOf(error), reference: referenceOf(error) });
        for (const [field, messages] of Object.entries(fieldErrorsOf(error))) {
          if (field !== "lines" && field in schema.shape && messages[0]) {
            setError(field as keyof Values, { type: "server", message: messages[0] });
          }
        }
      },
    },
  );

  function onSubmit(values: Values) {
    setFormError({ message: null, reference: null });
    const header = {
      order_date: values.order_date,
      notes: values.notes,
      terms: values.terms,
      lines: values.lines.map(toPricedLineInput),
    };
    if (order) {
      mutation.mutate(header);
      return;
    }
    mutation.mutate({ ...header, customer_id: values.customer_id, currency: org.currency });
  }

  return (
    <FormProvider {...form}>
      <form method="post" noValidate onSubmit={(event) => void handleSubmit(onSubmit)(event)} className="flex flex-col gap-4">
        <FormError message={formError.message} reference={formError.reference} />

        <Card>
          <CardHeader title="Sales order" />
          <CardBody className="grid gap-4 sm:grid-cols-2">
            <FormField
              label="Customer"
              required
              error={errors.customer_id?.message ?? null}
              {...(isEdit ? { hint: "Fixed once the draft is saved." } : {})}
            >
              <Controller
                control={control}
                name="customer_id"
                render={({ field }) => (
                  <CustomerPicker
                    value={field.value || null}
                    onChange={(value) => field.onChange(value ?? "")}
                    disabled={isEdit}
                  />
                )}
              />
            </FormField>

            <FormField label="Order date" required error={errors.order_date?.message ?? null}>
              <Input type="date" {...register("order_date")} />
            </FormField>
          </CardBody>
        </Card>

        <PricedLinesEditor
          usage="sales"
          description="Only tracked products can later be delivered on a challan; service lines are billed directly."
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
