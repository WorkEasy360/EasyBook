"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { Controller, useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { FormError, FormField, Input } from "@/components/ui/field";
import { QuantityInput } from "@/components/ui/numeric-input";
import { useToast } from "@/components/ui/toast";
import { ItemPicker, WarehousePicker } from "@/features/shared/pickers";
import { useIdempotentMutation } from "@/lib/hooks/use-api";
import { useUnsavedChanges } from "@/lib/hooks/use-unsaved-changes";
import { api } from "@/lib/api/browser";
import { fieldErrorsOf, formErrorOf, referenceOf } from "@/lib/api/errors";
import { isValidDecimal, money } from "@/lib/money";
import type { StockTransferInput, StockTransferResult } from "@/types/api/inventory";

/**
 * Move stock between warehouses.
 *
 * Unlike an adjustment there is no draft: the backend writes the paired out
 * and in movements immediately (inventory/services/transfers.py), so this is
 * an idempotent write and the button says what it does. Whether the source
 * warehouse holds enough stock is the backend's call — it answers from the
 * ledger at the moment of the transfer, which a figure loaded into this page
 * earlier could not.
 */

const schema = z
  .object({
    item_id: z.string().min(1, "Choose an item."),
    from_warehouse_id: z.string().min(1, "Choose the source warehouse."),
    to_warehouse_id: z.string().min(1, "Choose the destination warehouse."),
    quantity: z
      .string()
      .refine((value) => isValidDecimal(value) && money(value).gt(0), "Enter a quantity above zero."),
    notes: z.string().max(500),
  })
  .refine((values) => values.from_warehouse_id !== values.to_warehouse_id, {
    message: "Source and destination must be different warehouses.",
    path: ["to_warehouse_id"],
  });

type Values = z.infer<typeof schema>;

export function TransferForm({ initialItemId }: { initialItemId?: string }) {
  const router = useRouter();
  const toast = useToast();
  const [formError, setFormError] = React.useState<{ message: string | null; reference: string | null }>({
    message: null,
    reference: null,
  });

  const {
    control,
    register,
    handleSubmit,
    setError,
    formState: { errors, isDirty, isSubmitting },
  } = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: {
      item_id: initialItemId ?? "",
      from_warehouse_id: "",
      to_warehouse_id: "",
      quantity: "",
      notes: "",
    },
  });

  useUnsavedChanges(isDirty && !isSubmitting);

  const mutation = useIdempotentMutation<StockTransferResult, StockTransferInput>(
    "inventory/transfers",
    (input, idempotencyKey) => api.post<StockTransferResult>("inventory/transfers", input, { idempotencyKey }),
    {
      invalidates: "inventory",
      onSuccess: (result) => {
        toast.push({ tone: "success", title: "Stock transferred" });
        router.push(`/inventory/movements?item_id=${result.out_movement.item}`);
        router.refresh();
      },
      onError: (error) => {
        setFormError({ message: formErrorOf(error), reference: referenceOf(error) });
        for (const [field, messages] of Object.entries(fieldErrorsOf(error))) {
          if (field in schema.shape && messages[0]) {
            setError(field as keyof Values, { type: "server", message: messages[0] });
          }
        }
      },
    },
  );

  return (
    <form
      method="post"
      noValidate
      onSubmit={(event) =>
        void handleSubmit((values) => {
          setFormError({ message: null, reference: null });
          mutation.mutate(values);
        })(event)
      }
      className="flex flex-col gap-4"
    >
      <FormError message={formError.message} reference={formError.reference} />

      <Card>
        <CardHeader title="Transfer" description="Writes a transfer-out and a transfer-in movement at the same cost." />
        <CardBody className="grid gap-4 sm:grid-cols-2">
          <FormField label="Item" required error={errors.item_id?.message ?? null} className="sm:col-span-2">
            <Controller
              control={control}
              name="item_id"
              render={({ field }) => (
                <ItemPicker trackedOnly value={field.value || null} onChange={(value) => field.onChange(value ?? "")} />
              )}
            />
          </FormField>

          <FormField label="From warehouse" required error={errors.from_warehouse_id?.message ?? null}>
            <Controller
              control={control}
              name="from_warehouse_id"
              render={({ field }) => (
                <WarehousePicker value={field.value || null} onChange={(value) => field.onChange(value ?? "")} />
              )}
            />
          </FormField>

          <FormField label="To warehouse" required error={errors.to_warehouse_id?.message ?? null}>
            <Controller
              control={control}
              name="to_warehouse_id"
              render={({ field }) => (
                <WarehousePicker value={field.value || null} onChange={(value) => field.onChange(value ?? "")} />
              )}
            />
          </FormField>

          <FormField label="Quantity" required error={errors.quantity?.message ?? null}>
            <Controller
              control={control}
              name="quantity"
              render={({ field }) => (
                <QuantityInput value={field.value} onValueChange={field.onChange} onBlur={field.onBlur} />
              )}
            />
          </FormField>

          <FormField label="Notes" error={errors.notes?.message ?? null}>
            <Input {...register("notes")} />
          </FormField>
        </CardBody>
      </Card>

      <div className="flex items-center justify-end gap-2">
        <Button variant="secondary" onClick={() => router.back()} disabled={mutation.isPending}>
          Cancel
        </Button>
        <Button type="submit" variant="primary" loading={mutation.isPending} loadingLabel="Transferring">
          Transfer stock
        </Button>
      </div>
    </form>
  );
}
