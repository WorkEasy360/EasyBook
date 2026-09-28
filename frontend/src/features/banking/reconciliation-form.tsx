"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { Controller, useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { FormError, FormField, Input, Textarea } from "@/components/ui/field";
import { NumericInput } from "@/components/ui/numeric-input";
import { useToast } from "@/components/ui/toast";
import { useOrg } from "@/components/providers/org-provider";
import { useUnsavedChanges } from "@/lib/hooks/use-unsaved-changes";
import { useIdempotentMutation } from "@/lib/hooks/use-api";
import { api } from "@/lib/api/browser";
import { errorCodeOf, fieldErrorsOf, formErrorOf, referenceOf } from "@/lib/api/errors";
import { isValidDecimal } from "@/lib/money";
import { BankAccountPicker } from "./bank-account-picker";
import type { BankAccount, BankReconciliation, BankReconciliationInput } from "@/types/api/banking";

/**
 * Start a reconciliation for one statement period
 * (banking/services/reconciliation.py :: start_reconciliation).
 *
 * The closing balance is TYPED from the bank's own statement and deliberately
 * not pre-filled from the imported lines: a figure derived from those lines
 * would agree with them by construction and certify nothing. Starting posts
 * nothing and locks nothing; only completing does, and only when the server
 * says the period balances.
 */

const DATE = /^\d{4}-\d{2}-\d{2}$/;

const schema = z
  .object({
    bank_account_id: z.string().min(1, "Choose the account to reconcile."),
    statement_start_date: z.string().regex(DATE, "Enter the first day on the statement."),
    statement_end_date: z.string().regex(DATE, "Enter the last day on the statement."),
    statement_closing_balance: z.string().refine(isValidDecimal, "Enter the closing balance printed on the statement."),
    notes: z.string().max(4000),
  })
  // reconciliation_dates_invalid. ISO dates compare correctly as strings.
  .refine((values) => values.statement_end_date >= values.statement_start_date, {
    message: "The period cannot end before it starts.",
    path: ["statement_end_date"],
  });

type Values = z.infer<typeof schema>;

const CODE_FIELDS: Record<string, keyof Values> = {
  reconciliation_already_open: "bank_account_id",
  reconciliation_period_overlap: "statement_start_date",
  reconciliation_dates_invalid: "statement_end_date",
};

export function ReconciliationForm({ initialAccount }: { initialAccount?: BankAccount | undefined }) {
  const router = useRouter();
  const toast = useToast();
  const org = useOrg();
  const [account, setAccount] = React.useState<BankAccount | null>(initialAccount ?? null);
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
      bank_account_id: initialAccount?.id ?? "",
      statement_start_date: "",
      statement_end_date: "",
      statement_closing_balance: "",
      notes: "",
    },
  });
  useUnsavedChanges(isDirty && !isSubmitting);

  const mutation = useIdempotentMutation<BankReconciliation, BankReconciliationInput>(
    "bank-reconciliations",
    (input, idempotencyKey) => api.post<BankReconciliation>("bank-reconciliations", input, { idempotencyKey }),
    {
      onSuccess: (saved) => {
        toast.push({ tone: "success", title: "Reconciliation started" });
        router.push(`/banking/reconciliation/${saved.id}`);
        router.refresh();
      },
      onError: (error) => {
        setFormError({ message: formErrorOf(error), reference: referenceOf(error) });
        for (const [field, messages] of Object.entries(fieldErrorsOf(error))) {
          if (field in schema.shape && messages[0]) setError(field as keyof Values, { type: "server", message: messages[0] });
        }
        const code = errorCodeOf(error);
        const field = code ? CODE_FIELDS[code] : undefined;
        if (field) setError(field, { type: "server", message: error.message });
      },
    },
  );

  function onSubmit(values: Values) {
    setFormError({ message: null, reference: null });
    mutation.mutate({
      bank_account_id: values.bank_account_id,
      statement_start_date: values.statement_start_date,
      statement_end_date: values.statement_end_date,
      statement_closing_balance: values.statement_closing_balance,
      notes: values.notes,
    });
  }

  return (
    <form method="post" noValidate onSubmit={(event) => void handleSubmit(onSubmit)(event)} className="flex flex-col gap-4">
      <FormError message={formError.message} reference={formError.reference} />

      <Card>
        <CardHeader title="Statement period" description="Take these from the bank statement you are reconciling against." />
        <CardBody className="grid gap-4 sm:grid-cols-2">
          <FormField
            label="Bank account"
            required
            error={errors.bank_account_id?.message ?? null}
            hint="One reconciliation can be in progress per account."
            className="sm:col-span-2"
          >
            <Controller
              control={control}
              name="bank_account_id"
              render={({ field }) => (
                <BankAccountPicker
                  activeOnly={false}
                  value={field.value || null}
                  onChange={(value) => field.onChange(value ?? "")}
                  onSelectAccount={setAccount}
                />
              )}
            />
          </FormField>

          <FormField
            label="Statement start date"
            required
            error={errors.statement_start_date?.message ?? null}
            hint="Cannot overlap a period already completed."
          >
            <Input type="date" {...register("statement_start_date")} />
          </FormField>

          <FormField label="Statement end date" required error={errors.statement_end_date?.message ?? null}>
            <Input type="date" {...register("statement_end_date")} />
          </FormField>

          <FormField
            label="Closing balance on the statement"
            required
            error={errors.statement_closing_balance?.message ?? null}
            hint={
              account?.kind === "credit_card"
                ? "Type it from the statement. An amount owed on a card is negative."
                : "Type it from the statement. It is not filled in from imported lines on purpose — that would agree by construction and prove nothing."
            }
          >
            <Controller
              control={control}
              name="statement_closing_balance"
              render={({ field }) => (
                <NumericInput
                  currency={account?.currency ?? org.currency}
                  value={field.value}
                  onValueChange={field.onChange}
                  onBlur={field.onBlur}
                />
              )}
            />
          </FormField>

          <FormField label="Notes" className="sm:col-span-2" error={errors.notes?.message ?? null}>
            <Textarea rows={2} {...register("notes")} />
          </FormField>
        </CardBody>
      </Card>

      <div className="flex items-center justify-end gap-2">
        <Button variant="secondary" onClick={() => router.back()} disabled={mutation.isPending}>
          Cancel
        </Button>
        <Button type="submit" variant="primary" loading={mutation.isPending} loadingLabel="Starting">
          Start reconciliation
        </Button>
      </div>
    </form>
  );
}
