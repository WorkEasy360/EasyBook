"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Checkbox, FormError, FormField, Input, Textarea } from "@/components/ui/field";
import { useToast } from "@/components/ui/toast";
import { useApiMutation } from "@/lib/hooks/use-api";
import { api } from "@/lib/api/browser";
import { fieldErrorsOf, formErrorOf, referenceOf } from "@/lib/api/errors";
import type { Warehouse, WarehouseInput } from "@/types/api/inventory";

/**
 * Create or edit a warehouse.
 *
 * "Default" is decided by the backend's warehouse service, which keeps exactly
 * one default per organization. The form only asks for it; the list is
 * re-rendered from the server afterwards so the previous default's badge
 * disappears because the API says so, not because this component guessed.
 */

const schema = z.object({
  code: z.string().trim().min(1, "A code is required.").max(32),
  name: z.string().trim().min(1, "A name is required.").max(255),
  address: z.string().max(2000),
  is_default: z.boolean(),
  is_active: z.boolean(),
});

type Values = z.infer<typeof schema>;

export function WarehouseDialogButton({ warehouse }: { warehouse?: Warehouse }) {
  const router = useRouter();
  const toast = useToast();
  const [open, setOpen] = React.useState(false);
  const [formError, setFormError] = React.useState<{ message: string | null; reference: string | null }>({
    message: null,
    reference: null,
  });

  const {
    register,
    handleSubmit,
    reset,
    setError,
    formState: { errors },
  } = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: {
      code: warehouse?.code ?? "",
      name: warehouse?.name ?? "",
      address: warehouse?.address ?? "",
      is_default: warehouse?.is_default ?? false,
      is_active: warehouse?.is_active ?? true,
    },
  });

  const mutation = useApiMutation<Warehouse, WarehouseInput>(
    "inventory/warehouses",
    (input) =>
      warehouse
        ? api.patch<Warehouse>(`inventory/warehouses/${warehouse.id}`, input)
        : api.post<Warehouse>("inventory/warehouses", input),
    {
      invalidates: "inventory",
      onSuccess: (saved) => {
        toast.push({
          tone: "success",
          title: warehouse ? "Warehouse updated" : "Warehouse created",
          description: saved.name,
        });
        setOpen(false);
        if (!warehouse) reset();
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

  function onSubmit(values: Values) {
    setFormError({ message: null, reference: null });
    mutation.mutate({ ...values, code: values.code.toUpperCase() });
  }

  const formId = React.useId();

  return (
    <>
      {warehouse ? (
        <Button variant="link" size="sm" onClick={() => setOpen(true)}>
          Edit<span className="sr-only"> {warehouse.name}</span>
        </Button>
      ) : (
        <Button variant="primary" onClick={() => setOpen(true)}>
          New warehouse
        </Button>
      )}

      <Dialog
        open={open}
        onClose={() => {
          if (!mutation.isPending) setOpen(false);
        }}
        title={warehouse ? `Edit ${warehouse.name}` : "New warehouse"}
        dismissible={!mutation.isPending}
        footer={
          <>
            <Button variant="secondary" onClick={() => setOpen(false)} disabled={mutation.isPending}>
              Cancel
            </Button>
            <Button type="submit" form={formId} variant="primary" loading={mutation.isPending}>
              {warehouse ? "Save changes" : "Create warehouse"}
            </Button>
          </>
        }
      >
        <form
          id={formId}
          method="post"
          noValidate
          onSubmit={(event) => void handleSubmit(onSubmit)(event)}
          className="flex flex-col gap-4"
        >
          <FormError message={formError.message} reference={formError.reference} />
          <div className="grid gap-4 sm:grid-cols-3">
            <FormField label="Code" required error={errors.code?.message ?? null}>
              <Input className="tabular uppercase" {...register("code")} />
            </FormField>
            <FormField label="Name" required error={errors.name?.message ?? null} className="sm:col-span-2">
              <Input {...register("name")} />
            </FormField>
            <FormField label="Address" error={errors.address?.message ?? null} className="sm:col-span-3">
              <Textarea rows={2} {...register("address")} />
            </FormField>
          </div>
          <Checkbox
            label="Default warehouse"
            hint="Used when a document does not name a warehouse. Setting this replaces the current default."
            {...register("is_default")}
          />
          <Checkbox
            label="Active"
            hint="Inactive warehouses keep their history but cannot receive new stock."
            {...register("is_active")}
          />
        </form>
      </Dialog>
    </>
  );
}
