"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { Controller, useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { FormError, FormField, Input, Select } from "@/components/ui/field";
import { NumericInput } from "@/components/ui/numeric-input";
import { useToast } from "@/components/ui/toast";
import { useOrg } from "@/components/providers/org-provider";
import { useIdempotentMutation } from "@/lib/hooks/use-api";
import { api } from "@/lib/api/browser";
import { fieldErrorsOf, formErrorOf, referenceOf } from "@/lib/api/errors";
import { todayInZone } from "@/lib/datetime";
import { isPositiveAmount, signedAmount } from "./amounts";
import type { BankTransaction, BankTransactionInput } from "@/types/api/banking";

/**
 * Add one statement line by hand (banking/services/transactions.py ::
 * add_manual_transaction).
 *
 * This is the deliberate escape hatch from the importer's duplicate rule: an
 * identical charge on an identical day in a later statement looks exactly
 * like an overlapping export, so the importer skips it and a person adds it
 * here, with an author and an audit record. It posts nothing to the ledger.
 *
 * The amount is entered as a positive figure plus a direction and sent signed
 * (positive in, negative out) — a sign typed into a single box is the easiest
 * way to book a withdrawal as a deposit.
 */

const schema = z.object({
  transaction_date: z.string().regex(/^\d{4}-\d{2}-\d{2}$/, "Enter the date on the statement."),
  direction: z.enum(["in", "out"]),
  amount: z.string().refine(isPositiveAmount, "Enter an amount above zero."),
  description: z.string().max(4000),
  counterparty_name: z.string().max(255),
  bank_reference: z.string().max(255),
  external_id: z.string().max(255),
});

type Values = z.infer<typeof schema>;

export function ManualTransactionButton({ bankAccountId, currency }: { bankAccountId: string; currency: string }) {
  const router = useRouter();
  const toast = useToast();
  const org = useOrg();
  const [open, setOpen] = React.useState(false);
  const [formError, setFormError] = React.useState<{ message: string | null; reference: string | null }>({
    message: null,
    reference: null,
  });

  const defaults: Values = {
    transaction_date: todayInZone(org.timeZone),
    direction: "out",
    amount: "",
    description: "",
    counterparty_name: "",
    bank_reference: "",
    external_id: "",
  };

  const {
    control,
    register,
    handleSubmit,
    reset,
    setError,
    formState: { errors },
  } = useForm<Values>({ resolver: zodResolver(schema), defaultValues: defaults });

  const mutation = useIdempotentMutation<BankTransaction, BankTransactionInput>(
    "bank-transactions",
    (input, idempotencyKey) =>
      api.post<BankTransaction>(`bank-accounts/${bankAccountId}/transactions`, input, { idempotencyKey }),
    {
      onSuccess: () => {
        toast.push({ tone: "success", title: "Statement line added", description: "Match or categorize it to explain it." });
        setOpen(false);
        reset(defaults);
        router.refresh();
      },
      onError: (error) => {
        setFormError({ message: formErrorOf(error), reference: referenceOf(error) });
        for (const [field, messages] of Object.entries(fieldErrorsOf(error))) {
          if (field in schema.shape && messages[0]) setError(field as keyof Values, { type: "server", message: messages[0] });
        }
      },
    },
  );

  function onSubmit(values: Values) {
    setFormError({ message: null, reference: null });
    mutation.mutate({
      transaction_date: values.transaction_date,
      amount: signedAmount(values.amount, values.direction),
      description: values.description,
      counterparty_name: values.counterparty_name,
      bank_reference: values.bank_reference,
      external_id: values.external_id,
    });
  }

  const formId = React.useId();

  return (
    <>
      <Button onClick={() => setOpen(true)}>Add transaction</Button>
      <Dialog
        open={open}
        onClose={() => {
          if (!mutation.isPending) setOpen(false);
        }}
        title="Add a statement line"
        description="For a line the importer skipped as a duplicate, or one from a paper statement. Nothing is posted to the ledger."
        dismissible={!mutation.isPending}
        footer={
          <>
            <Button variant="secondary" onClick={() => setOpen(false)} disabled={mutation.isPending}>
              Cancel
            </Button>
            <Button type="submit" form={formId} variant="primary" loading={mutation.isPending}>
              Add line
            </Button>
          </>
        }
      >
        <form id={formId} method="post" noValidate onSubmit={(event) => void handleSubmit(onSubmit)(event)} className="flex flex-col gap-4">
          <FormError message={formError.message} reference={formError.reference} />
          <div className="grid gap-4 sm:grid-cols-2">
            <FormField label="Date" required error={errors.transaction_date?.message ?? null}>
              <Input type="date" {...register("transaction_date")} />
            </FormField>
            <FormField label="Direction" required>
              <Select {...register("direction")}>
                <option value="out">Money out</option>
                <option value="in">Money in</option>
              </Select>
            </FormField>
            <FormField label="Amount" required error={errors.amount?.message ?? null} className="sm:col-span-2">
              <Controller
                control={control}
                name="amount"
                render={({ field }) => (
                  <NumericInput
                    currency={currency}
                    nonNegative
                    value={field.value}
                    onValueChange={field.onChange}
                    onBlur={field.onBlur}
                  />
                )}
              />
            </FormField>
            <FormField label="Description" error={errors.description?.message ?? null} className="sm:col-span-2">
              <Input {...register("description")} />
            </FormField>
            <FormField label="Counterparty" error={errors.counterparty_name?.message ?? null}>
              <Input {...register("counterparty_name")} />
            </FormField>
            <FormField label="Bank reference" error={errors.bank_reference?.message ?? null}>
              <Input {...register("bank_reference")} />
            </FormField>
            <FormField
              label="Bank transaction id"
              error={errors.external_id?.message ?? null}
              className="sm:col-span-2"
              hint="Only if the bank issued one. It becomes this line's duplicate guard."
            >
              <Input {...register("external_id")} />
            </FormField>
          </div>
        </form>
      </Dialog>
    </>
  );
}
