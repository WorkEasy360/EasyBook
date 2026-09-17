"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Checkbox, FormError, FormField, Input } from "@/components/ui/field";
import { useToast } from "@/components/ui/toast";
import { useApiMutation } from "@/lib/hooks/use-api";
import { api } from "@/lib/api/browser";
import { fieldErrorsOf, formErrorOf, referenceOf } from "@/lib/api/errors";
import type { UnitOfMeasure, UnitOfMeasureInput } from "@/types/api/items";

/**
 * Create or edit a unit of measure in a dialog — a unit is three short fields,
 * not worth a page of its own.
 *
 * System units are seeded by the backend and their code is protected, so the
 * code is read-only for them rather than offered and then rejected.
 */

const schema = z.object({
  code: z.string().trim().min(1, "A code is required.").max(16),
  name: z.string().trim().min(1, "A name is required.").max(64),
  symbol: z.string().trim().max(16),
  is_active: z.boolean(),
});

type Values = z.infer<typeof schema>;

export function UnitDialogButton({ unit }: { unit?: UnitOfMeasure }) {
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
      code: unit?.code ?? "",
      name: unit?.name ?? "",
      symbol: unit?.symbol ?? "",
      is_active: unit?.is_active ?? true,
    },
  });

  const mutation = useApiMutation<UnitOfMeasure, Partial<UnitOfMeasureInput>>(
    "items/units",
    (input) =>
      unit
        ? api.patch<UnitOfMeasure>(`items/units/${unit.id}`, input)
        : api.post<UnitOfMeasure>("items/units", input),
    {
      invalidates: "items",
      onSuccess: (saved) => {
        toast.push({ tone: "success", title: unit ? "Unit updated" : "Unit created", description: saved.name });
        setOpen(false);
        if (!unit) reset();
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
    mutation.mutate({
      // A system unit's code is protected; do not send it at all.
      ...(unit?.is_system ? {} : { code: values.code.toUpperCase() }),
      name: values.name,
      symbol: values.symbol,
      is_active: values.is_active,
    });
  }

  const formId = React.useId();

  return (
    <>
      {unit ? (
        <Button variant="link" size="sm" onClick={() => setOpen(true)}>
          Edit<span className="sr-only"> {unit.name}</span>
        </Button>
      ) : (
        <Button variant="primary" onClick={() => setOpen(true)}>
          New unit
        </Button>
      )}

      <Dialog
        open={open}
        onClose={() => {
          if (!mutation.isPending) setOpen(false);
        }}
        title={unit ? `Edit ${unit.name}` : "New unit of measure"}
        dismissible={!mutation.isPending}
        footer={
          <>
            <Button variant="secondary" onClick={() => setOpen(false)} disabled={mutation.isPending}>
              Cancel
            </Button>
            <Button type="submit" form={formId} variant="primary" loading={mutation.isPending}>
              {unit ? "Save changes" : "Create unit"}
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
          <div className="grid gap-4 sm:grid-cols-2">
            <FormField
              label="Code"
              required
              error={errors.code?.message ?? null}
              hint={unit?.is_system ? "System units keep their code." : "Short and unique, e.g. PCS."}
            >
              <Input className="tabular uppercase" readOnly={unit?.is_system} {...register("code")} />
            </FormField>
            <FormField label="Symbol" error={errors.symbol?.message ?? null} hint="Shown after quantities.">
              <Input {...register("symbol")} />
            </FormField>
            <FormField label="Name" required error={errors.name?.message ?? null} className="sm:col-span-2">
              <Input {...register("name")} />
            </FormField>
          </div>
          <Checkbox
            label="Active"
            hint="Inactive units stay on existing items but cannot be chosen for new ones."
            {...register("is_active")}
          />
        </form>
      </Dialog>
    </>
  );
}
