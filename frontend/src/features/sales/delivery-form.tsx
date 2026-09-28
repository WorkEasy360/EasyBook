"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { Controller, useFieldArray, useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { Button, IconButton } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { FormError, FormField, Input, Textarea } from "@/components/ui/field";
import { QuantityInput } from "@/components/ui/numeric-input";
import { Quantity } from "@/components/ui/money";
import { Icons } from "@/components/ui/icons";
import { useToast } from "@/components/ui/toast";
import { useOrg } from "@/components/providers/org-provider";
import { CustomerPicker, ItemPicker, WarehousePicker } from "@/features/shared/pickers";
import { useUnsavedChanges } from "@/lib/hooks/use-unsaved-changes";
import { useIdempotentMutation } from "@/lib/hooks/use-api";
import { api } from "@/lib/api/browser";
import { fieldErrorsOf, formErrorOf, referenceOf } from "@/lib/api/errors";
import { isValidDecimal, money } from "@/lib/money";
import { todayInZone } from "@/lib/datetime";
import type { LineFulfillment } from "./fulfillment";
import type {
  DeliveryChallan,
  DeliveryChallanInput,
  DeliveryChallanUpdateInput,
} from "@/types/api/sales";

/**
 * Create or edit a DRAFT delivery challan.
 *
 * Saving moves no stock. Stock leaves the warehouse only when the challan is
 * dispatched (sales/services/deliveries.py :: dispatch_delivery), which is a
 * separate, confirmed action on the challan page.
 *
 * Only inventory-tracked products can be delivered (item_not_deliverable),
 * so the item picker offers nothing else. A line linked to a sales-order line
 * keeps that link and its item; the backend refuses a quantity that would
 * take the order line past what was ordered (over_fulfillment).
 *
 * After creation the backend accepts only the date, notes and lines
 * (DeliveryChallanDetailView.update); customer, warehouse and source order are
 * shown read-only.
 */

const lineSchema = z.object({
  item_id: z.string().min(1, "Choose an item."),
  description: z.string().max(500),
  quantity: z.string().refine((value) => isValidDecimal(value) && money(value).gt(0), "Enter a quantity above zero."),
  source_order_line_id: z.string().nullable(),
});

const schema = z.object({
  customer_id: z.string().min(1, "Choose a customer."),
  warehouse_id: z.string().min(1, "Choose the warehouse the goods leave from."),
  challan_date: z.string().regex(/^\d{4}-\d{2}-\d{2}$/, "Enter the challan date."),
  notes: z.string().max(4000),
  lines: z.array(lineSchema).min(1, "Add at least one line."),
});

type Values = z.infer<typeof schema>;

const EMPTY_LINE: Values["lines"][number] = { item_id: "", description: "", quantity: "1", source_order_line_id: null };

export interface DeliveryFormInitial {
  customer_id?: string;
  warehouse_id?: string | null;
  source_sales_order_id?: string | null;
  lines?: Values["lines"];
}

export function DeliveryForm({
  challan,
  initial = {},
  orderLabel,
  fulfillment = {},
}: {
  challan?: DeliveryChallan;
  initial?: DeliveryFormInitial;
  /** e.g. "SO-0001", when the challan delivers against an order. */
  orderLabel?: string | null;
  /** Per sales-order line, what has already gone out — for the hints only. */
  fulfillment?: Record<string, LineFulfillment>;
}) {
  const router = useRouter();
  const toast = useToast();
  const org = useOrg();
  const isEdit = Boolean(challan);
  const [formError, setFormError] = React.useState<{ message: string | null; reference: string | null }>({
    message: null,
    reference: null,
  });

  const sourceOrderId = challan ? challan.source_sales_order : (initial.source_sales_order_id ?? null);

  const {
    control,
    register,
    handleSubmit,
    setError,
    formState: { errors, isDirty, isSubmitting },
  } = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: challan
      ? {
          customer_id: challan.customer,
          warehouse_id: challan.warehouse,
          challan_date: challan.challan_date,
          notes: challan.notes,
          lines: challan.lines.map((line) => ({
            item_id: line.item,
            description: line.description,
            quantity: line.quantity,
            source_order_line_id: line.source_order_line,
          })),
        }
      : {
          customer_id: initial.customer_id ?? "",
          warehouse_id: initial.warehouse_id ?? "",
          challan_date: todayInZone(org.timeZone),
          notes: "",
          lines: initial.lines?.length ? initial.lines : [{ ...EMPTY_LINE }],
        },
  });

  const { fields, append, remove } = useFieldArray({ control, name: "lines" });
  useUnsavedChanges(isDirty && !isSubmitting);

  const mutation = useIdempotentMutation<DeliveryChallan, DeliveryChallanInput | DeliveryChallanUpdateInput>(
    "sales/deliveries",
    (input, idempotencyKey) =>
      challan
        ? api.patch<DeliveryChallan>(`sales/deliveries/${challan.id}`, input)
        : api.post<DeliveryChallan>("sales/deliveries", input, { idempotencyKey }),
    {
      onSuccess: (saved) => {
        toast.push({
          tone: "success",
          title: isEdit ? "Draft challan saved" : `Delivery challan ${saved.challan_number} created`,
        });
        router.push(`/sales/deliveries/${saved.id}`);
        router.refresh();
      },
      onError: (error) => {
        const fieldErrors = fieldErrorsOf(error);
        const lineMessages = fieldErrors["lines"];
        setFormError({
          message: lineMessages?.length ? `Lines: ${lineMessages.join(" ")}` : formErrorOf(error),
          reference: referenceOf(error),
        });
        for (const [field, messages] of Object.entries(fieldErrors)) {
          if (field !== "lines" && field in schema.shape && messages[0]) {
            setError(field as keyof Values, { type: "server", message: messages[0] });
          }
        }
      },
    },
  );

  function onSubmit(values: Values) {
    setFormError({ message: null, reference: null });
    const lines = values.lines.map((line) => ({
      item_id: line.item_id,
      description: line.description.trim(),
      quantity: line.quantity,
      source_order_line_id: line.source_order_line_id,
    }));
    if (challan) {
      mutation.mutate({ challan_date: values.challan_date, notes: values.notes, lines });
      return;
    }
    mutation.mutate({
      customer_id: values.customer_id,
      warehouse_id: values.warehouse_id,
      challan_date: values.challan_date,
      source_sales_order_id: sourceOrderId,
      notes: values.notes,
      lines,
    });
  }

  const lockedHint = isEdit ? "Fixed once the draft is saved." : undefined;

  return (
    <form method="post" noValidate onSubmit={(event) => void handleSubmit(onSubmit)(event)} className="flex flex-col gap-4">
      <FormError message={formError.message} reference={formError.reference} />

      <Card>
        <CardHeader
          title="Delivery challan"
          {...(orderLabel ? { description: `Delivering against sales order ${orderLabel}.` } : {})}
        />
        <CardBody className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <FormField
            label="Customer"
            required
            error={errors.customer_id?.message ?? null}
            {...(lockedHint ? { hint: lockedHint } : sourceOrderId ? { hint: "The sales order's customer." } : {})}
          >
            <Controller
              control={control}
              name="customer_id"
              render={({ field }) => (
                <CustomerPicker
                  value={field.value || null}
                  onChange={(value) => field.onChange(value ?? "")}
                  disabled={isEdit || Boolean(sourceOrderId)}
                />
              )}
            />
          </FormField>

          <FormField
            label="Warehouse"
            required
            error={errors.warehouse_id?.message ?? null}
            hint={lockedHint ?? "Stock is issued from here when the challan is dispatched."}
          >
            <Controller
              control={control}
              name="warehouse_id"
              render={({ field }) => (
                <WarehousePicker value={field.value || null} onChange={(value) => field.onChange(value ?? "")} disabled={isEdit} />
              )}
            />
          </FormField>

          <FormField label="Challan date" required error={errors.challan_date?.message ?? null} hint="Also the date of the stock movement on dispatch.">
            <Input type="date" {...register("challan_date")} />
          </FormField>

          <FormField label="Notes" className="sm:col-span-2 lg:col-span-3" error={errors.notes?.message ?? null}>
            <Textarea rows={2} {...register("notes")} />
          </FormField>
        </CardBody>
      </Card>

      <Card>
        <CardHeader
          title="Lines"
          description="Quantities only — a challan carries no prices. Only products that track inventory can be delivered."
          actions={
            <Button
              variant="secondary"
              size="sm"
              leadingIcon={<Icons.plus className="size-3.5" />}
              onClick={() => append({ ...EMPTY_LINE })}
            >
              Add line
            </Button>
          }
        />
        <CardBody className="flex flex-col gap-3">
          {errors.lines?.root?.message || errors.lines?.message ? (
            <p role="alert" className="text-xs text-danger-600">
              {errors.lines?.root?.message ?? errors.lines?.message}
            </p>
          ) : null}

          {fields.map((field, index) => {
            const lineErrors = errors.lines?.[index];
            const linked = field.source_order_line_id;
            const progress = linked ? fulfillment[linked] : undefined;
            return (
              <fieldset key={field.id} className="grid gap-3 rounded-md border border-ink-200 p-3 sm:grid-cols-12">
                <legend className="sr-only">Line {index + 1}</legend>

                <FormField
                  label="Item"
                  required
                  error={lineErrors?.item_id?.message ?? null}
                  className="sm:col-span-5"
                  {...(linked ? { hint: "From the sales order line." } : {})}
                >
                  <Controller
                    control={control}
                    name={`lines.${index}.item_id`}
                    render={({ field: item }) => (
                      <ItemPicker
                        usage="sales"
                        trackedOnly
                        value={item.value || null}
                        onChange={(value) => item.onChange(value ?? "")}
                        disabled={Boolean(linked)}
                      />
                    )}
                  />
                </FormField>

                <FormField
                  label="Quantity"
                  required
                  error={lineErrors?.quantity?.message ?? null}
                  className="sm:col-span-3"
                  {...(progress
                    ? {
                        hint: (
                          <>
                            Ordered <Quantity value={progress.ordered} />, dispatched <Quantity value={progress.fulfilled} />
                            {money(progress.inDrafts).gt(0) ? (
                              <>
                                , <Quantity value={progress.inDrafts} /> on other draft challans
                              </>
                            ) : null}
                            .
                          </>
                        ),
                      }
                    : {})}
                >
                  <Controller
                    control={control}
                    name={`lines.${index}.quantity`}
                    render={({ field: quantity }) => (
                      <QuantityInput value={quantity.value} onValueChange={quantity.onChange} onBlur={quantity.onBlur} />
                    )}
                  />
                </FormField>

                <FormField label="Description" className="sm:col-span-3" error={lineErrors?.description?.message ?? null}>
                  <Input {...register(`lines.${index}.description`)} />
                </FormField>

                <div className="flex items-end justify-end sm:col-span-1">
                  <IconButton
                    label={`Remove line ${index + 1}`}
                    icon={<Icons.close className="size-4" />}
                    disabled={fields.length === 1}
                    onClick={() => remove(index)}
                  />
                </div>
              </fieldset>
            );
          })}
        </CardBody>
      </Card>

      <div className="flex items-center justify-end gap-2">
        <Button variant="secondary" onClick={() => router.back()} disabled={mutation.isPending}>
          Cancel
        </Button>
        <Button type="submit" variant="primary" loading={mutation.isPending} loadingLabel="Saving">
          {isEdit ? "Save draft" : "Save as draft"}
        </Button>
      </div>
    </form>
  );
}
