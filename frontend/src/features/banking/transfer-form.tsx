"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { Controller, useForm, useWatch } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Dialog } from "@/components/ui/dialog";
import { FormError, FormField, Input, Textarea } from "@/components/ui/field";
import { Money } from "@/components/ui/money";
import { NumericInput } from "@/components/ui/numeric-input";
import { useToast } from "@/components/ui/toast";
import { useOrg } from "@/components/providers/org-provider";
import { useUnsavedChanges } from "@/lib/hooks/use-unsaved-changes";
import { useIdempotentMutation } from "@/lib/hooks/use-api";
import { api } from "@/lib/api/browser";
import { errorCodeOf, fieldErrorsOf, formErrorOf, referenceOf } from "@/lib/api/errors";
import { formatDate, todayInZone } from "@/lib/datetime";
import { isPositiveAmount } from "./amounts";
import { BankAccountPicker } from "./bank-account-picker";
import type { BankAccount, BankTransfer, BankTransferInput } from "@/types/api/banking";

/**
 * Record money moved between two of the organization's own accounts
 * (banking/services/transfers.py :: record_transfer).
 *
 * There is no draft: a transfer is a fact when recorded, and saving posts its
 * journal at once — debit the destination's ledger account, credit the
 * source's. Nothing touches income or expense. Because saving is the posting,
 * the form asks for confirmation with the accounts and amount spelled out,
 * and a mistake is corrected by voiding (a reversing journal), not editing.
 */

const schema = z
  .object({
    from_bank_account_id: z.string().min(1, "Choose the account the money left."),
    to_bank_account_id: z.string().min(1, "Choose the account the money arrived in."),
    transfer_date: z.string().regex(/^\d{4}-\d{2}-\d{2}$/, "Enter the transfer date."),
    amount: z.string().refine(isPositiveAmount, "Enter an amount above zero."),
    reference: z.string().max(255),
    description: z.string().max(4000),
  })
  .refine((values) => values.from_bank_account_id !== values.to_bank_account_id, {
    message: "A transfer needs two different accounts.",
    path: ["to_bank_account_id"],
  });

type Values = z.infer<typeof schema>;

const CODE_FIELDS: Record<string, keyof Values> = {
  transfer_same_account: "to_bank_account_id",
  transfer_currency_mismatch: "to_bank_account_id",
  transfer_amount_invalid: "amount",
};

export function TransferForm({ initialFrom }: { initialFrom?: BankAccount | undefined }) {
  const router = useRouter();
  const toast = useToast();
  const org = useOrg();
  const [pending, setPending] = React.useState<Values | null>(null);
  const [from, setFrom] = React.useState<BankAccount | null>(initialFrom ?? null);
  const [to, setTo] = React.useState<BankAccount | null>(null);
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
      from_bank_account_id: initialFrom?.id ?? "",
      to_bank_account_id: "",
      transfer_date: todayInZone(org.timeZone),
      amount: "",
      reference: "",
      description: "",
    },
  });
  useUnsavedChanges(isDirty && !isSubmitting);
  const fromId = useWatch({ control, name: "from_bank_account_id" });

  const mutation = useIdempotentMutation<BankTransfer, BankTransferInput>(
    "bank-transfers",
    (input, idempotencyKey) => api.post<BankTransfer>("bank-transfers", input, { idempotencyKey }),
    {
      onSuccess: (saved) => {
        setPending(null);
        toast.push({ tone: "success", title: `Transfer ${saved.transfer_number} recorded`, description: "Its journal is posted." });
        router.push("/banking/transfers");
        router.refresh();
      },
      onError: (error) => {
        setPending(null);
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

  const currencyMismatch = Boolean(from && to && from.currency !== to.currency);
  const currency = from?.currency ?? org.currency;

  return (
    <form
      method="post"
      noValidate
      onSubmit={(event) => {
        setFormError({ message: null, reference: null });
        void handleSubmit((values) => setPending(values))(event);
      }}
      className="flex flex-col gap-4"
    >
      <FormError message={formError.message} reference={formError.reference} />

      <Card>
        <CardHeader title="Transfer" />
        <CardBody className="grid gap-4 sm:grid-cols-2">
          <FormField label="From account" required error={errors.from_bank_account_id?.message ?? null} hint="Where the money left.">
            <Controller
              control={control}
              name="from_bank_account_id"
              render={({ field }) => (
                <BankAccountPicker
                  value={field.value || null}
                  onChange={(value) => field.onChange(value ?? "")}
                  onSelectAccount={setFrom}
                />
              )}
            />
          </FormField>

          <FormField
            label="To account"
            required
            error={errors.to_bank_account_id?.message ?? null}
            hint={
              currencyMismatch
                ? "These accounts are in different currencies. Transfers between currencies are not supported yet."
                : "Where the money arrived. Both accounts must use the same currency."
            }
          >
            <Controller
              control={control}
              name="to_bank_account_id"
              render={({ field }) => (
                <BankAccountPicker
                  value={field.value || null}
                  onChange={(value) => field.onChange(value ?? "")}
                  excludeId={fromId || null}
                  onSelectAccount={setTo}
                />
              )}
            />
          </FormField>

          <FormField label="Date" required error={errors.transfer_date?.message ?? null}>
            <Input type="date" {...register("transfer_date")} />
          </FormField>

          <FormField label="Amount" required error={errors.amount?.message ?? null}>
            <Controller
              control={control}
              name="amount"
              render={({ field }) => (
                <NumericInput currency={currency} nonNegative value={field.value} onValueChange={field.onChange} onBlur={field.onBlur} />
              )}
            />
          </FormField>

          <FormField label="Reference" error={errors.reference?.message ?? null} hint="A UTR or cheque number helps matching later.">
            <Input {...register("reference")} />
          </FormField>

          <FormField label="Description" className="sm:col-span-2" error={errors.description?.message ?? null}>
            <Textarea rows={2} {...register("description")} />
          </FormField>
        </CardBody>
      </Card>

      <div className="flex items-center justify-end gap-2">
        <Button variant="secondary" onClick={() => router.back()} disabled={mutation.isPending}>
          Cancel
        </Button>
        <Button type="submit" variant="primary" disabled={currencyMismatch}>
          Record transfer
        </Button>
      </div>

      <Dialog
        open={pending !== null}
        onClose={() => {
          if (!mutation.isPending) setPending(null);
        }}
        title="Record and post this transfer?"
        size="sm"
        dismissible={!mutation.isPending}
        footer={
          <>
            <Button variant="secondary" onClick={() => setPending(null)} disabled={mutation.isPending}>
              Cancel
            </Button>
            <Button
              variant="primary"
              loading={mutation.isPending}
              onClick={() => {
                if (pending) mutation.mutate(pending);
              }}
            >
              Record transfer
            </Button>
          </>
        }
      >
        {pending ? (
          <div className="flex flex-col gap-2 text-sm text-ink-700">
            <p>
              Moves <Money value={pending.amount} currency={currency} /> from {from?.name ?? "the source account"} to{" "}
              {to?.name ?? "the destination account"} on {formatDate(pending.transfer_date)}.
            </p>
            <p>
              A journal is posted now: debit {to?.name ?? "the destination"}&apos;s ledger account, credit{" "}
              {from?.name ?? "the source"}&apos;s. It is not income or expense.
            </p>
            <p>There is no draft. To correct a transfer, void it — that posts a reversing journal.</p>
          </div>
        ) : null}
      </Dialog>
    </form>
  );
}
