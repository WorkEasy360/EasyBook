"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { Controller, useForm, useWatch } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Checkbox, FormError, FormField, Input, Select, Textarea } from "@/components/ui/field";
import { NumericInput } from "@/components/ui/numeric-input";
import { BANK_ACCOUNT_KIND_LABELS } from "@/components/ui/status-badge";
import { useToast } from "@/components/ui/toast";
import { useOrg } from "@/components/providers/org-provider";
import { AccountPicker } from "@/features/shared/pickers";
import { useUnsavedChanges } from "@/lib/hooks/use-unsaved-changes";
import { useIdempotentMutation } from "@/lib/hooks/use-api";
import { api } from "@/lib/api/browser";
import { errorCodeOf, fieldErrorsOf, formErrorOf, referenceOf } from "@/lib/api/errors";
import { isValidDecimal, isZero } from "@/lib/money";
import type { BankAccount, BankAccountInput, BankAccountKind, BankAccountUpdateInput } from "@/types/api/banking";

/**
 * Create or edit a bank or credit card account.
 *
 * The contract this form follows (banking/services/bank_accounts.py):
 *  - Each bank account is paired 1:1 with a ledger account of the right type:
 *    asset for a bank, liability for a card (invalid_account_type,
 *    account_already_linked). The pairing and the kind are fixed once saved —
 *    both are "make a new bank account" operations — so they are shown
 *    disabled on edit rather than offered and then refused.
 *  - The account number is WRITE-ONLY. The service keeps only its last four
 *    digits, so the input is never pre-filled and nothing typed here is echoed
 *    anywhere; the page shows the server's `masked_number`.
 *  - The opening balance is the STATEMENT balance at go-live, not the ledger's
 *    opening balance, and it locks once any statement line exists
 *    (opening_balance_locked). The PATCH omits it when locked, because the
 *    service refuses the key even when the value is unchanged.
 */

const DATE = /^\d{4}-\d{2}-\d{2}$/;

const schema = z
  .object({
    name: z.string().trim().min(1, "Give the account a name.").max(255),
    kind: z.enum(["bank", "credit_card"]),
    account_id: z.string().min(1, "Choose the ledger account this bank account posts to."),
    currency_code: z
      .string()
      .trim()
      .regex(/^[A-Za-z]{3}$|^$/, "Use a three-letter currency code."),
    bank_name: z.string().max(255),
    account_number: z.string().max(64),
    branch_identifier: z.string().max(32),
    opening_balance: z.string().refine((value) => value === "" || isValidDecimal(value), "Enter an amount."),
    opening_balance_date: z.string().refine((value) => value === "" || DATE.test(value), "Enter a date."),
    notes: z.string().max(4000),
    is_active: z.boolean(),
  })
  // opening_balance_date_required: a balance is only meaningful ON a date.
  .refine(
    (values) => values.opening_balance === "" || isZero(values.opening_balance) || values.opening_balance_date !== "",
    { message: "An opening balance needs the date it was the balance on.", path: ["opening_balance_date"] },
  );

type Values = z.infer<typeof schema>;

/** Service error codes that belong to one input rather than the whole form. */
const CODE_FIELDS: Record<string, keyof Values> = {
  invalid_account_type: "account_id",
  account_already_linked: "account_id",
  account_not_found: "account_id",
  bank_account_name_taken: "name",
  opening_balance_date_required: "opening_balance_date",
  opening_balance_locked: "opening_balance",
  currency_not_found: "currency_code",
};

export function BankAccountForm({
  bankAccount,
  openingBalanceLocked = false,
}: {
  bankAccount?: BankAccount;
  /** True when statement lines exist, so the opening balance can no longer change. */
  openingBalanceLocked?: boolean;
}) {
  const router = useRouter();
  const toast = useToast();
  const org = useOrg();
  const isEdit = Boolean(bankAccount);
  const [formError, setFormError] = React.useState<{ message: string | null; reference: string | null }>({
    message: null,
    reference: null,
  });

  const {
    control,
    register,
    handleSubmit,
    setError,
    setValue,
    formState: { errors, isDirty, isSubmitting },
  } = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: {
      name: bankAccount?.name ?? "",
      kind: bankAccount?.kind ?? "bank",
      account_id: bankAccount?.account ?? "",
      currency_code: bankAccount?.currency ?? org.currency,
      bank_name: bankAccount?.bank_name ?? "",
      account_number: "",
      branch_identifier: bankAccount?.branch_identifier ?? "",
      opening_balance: bankAccount?.opening_balance ?? "",
      opening_balance_date: bankAccount?.opening_balance_date ?? "",
      notes: bankAccount?.notes ?? "",
      is_active: bankAccount?.is_active ?? true,
    },
  });

  useUnsavedChanges(isDirty && !isSubmitting);
  const kind = useWatch({ control, name: "kind" });

  const mutation = useIdempotentMutation<BankAccount, BankAccountInput | BankAccountUpdateInput>(
    "bank-accounts",
    (input, idempotencyKey) =>
      bankAccount
        ? api.patch<BankAccount>(`bank-accounts/${bankAccount.id}`, input)
        : api.post<BankAccount>("bank-accounts", input, { idempotencyKey }),
    {
      onSuccess: (saved) => {
        toast.push({ tone: "success", title: isEdit ? "Bank account saved" : "Bank account created", description: saved.name });
        router.push(`/banking/accounts/${saved.id}`);
        router.refresh();
      },
      onError: (error) => {
        setFormError({ message: formErrorOf(error), reference: referenceOf(error) });
        for (const [field, messages] of Object.entries(fieldErrorsOf(error))) {
          if (field in schema.shape && messages[0]) {
            setError(field as keyof Values, { type: "server", message: messages[0] });
          }
        }
        const code = errorCodeOf(error);
        const field = code ? CODE_FIELDS[code] : undefined;
        if (field) setError(field, { type: "server", message: error.message });
      },
    },
  );

  function onSubmit(values: Values) {
    setFormError({ message: null, reference: null });
    const openingBalance = values.opening_balance === "" ? "0.00" : values.opening_balance;
    const openingDate = values.opening_balance_date === "" ? null : values.opening_balance_date;

    if (bankAccount) {
      // PATCH only what changed: the service audits each changed field, and
      // resending an unchanged opening balance after lines exist is refused.
      const patch: BankAccountUpdateInput = {};
      if (values.name !== bankAccount.name) patch.name = values.name;
      if (values.bank_name !== bankAccount.bank_name) patch.bank_name = values.bank_name;
      if (values.branch_identifier !== bankAccount.branch_identifier) patch.branch_identifier = values.branch_identifier;
      if (values.notes !== bankAccount.notes) patch.notes = values.notes;
      if (values.is_active !== bankAccount.is_active) patch.is_active = values.is_active;
      // Blank keeps the stored digits; the API would otherwise clear them.
      if (values.account_number.trim() !== "") patch.account_number = values.account_number;
      if (!openingBalanceLocked) {
        if (openingBalance !== bankAccount.opening_balance) patch.opening_balance = openingBalance;
        if (openingDate !== bankAccount.opening_balance_date) patch.opening_balance_date = openingDate;
      }
      mutation.mutate(patch);
      return;
    }

    mutation.mutate({
      name: values.name,
      kind: values.kind,
      account_id: values.account_id,
      // The organization's own currency is the server default; send a code only when it differs.
      currency_code: values.currency_code.toUpperCase() === org.currency ? "" : values.currency_code.toUpperCase(),
      bank_name: values.bank_name,
      account_number: values.account_number,
      branch_identifier: values.branch_identifier,
      opening_balance: openingBalance,
      opening_balance_date: openingDate,
      notes: values.notes,
    });
  }

  const fixedHint = isEdit ? "Fixed once the account exists — create a new bank account instead." : undefined;
  const lockedHint = openingBalanceLocked
    ? "Locked: statement lines exist, and every statement and cleared balance is measured from this figure."
    : undefined;

  return (
    <form method="post" noValidate onSubmit={(event) => void handleSubmit(onSubmit)(event)} className="flex flex-col gap-4">
      <FormError message={formError.message} reference={formError.reference} />

      <Card>
        <CardHeader title="Account" />
        <CardBody className="grid gap-4 sm:grid-cols-2">
          <FormField label="Name" required error={errors.name?.message ?? null} hint="How this account appears across EasyBook.">
            <Input {...register("name")} />
          </FormField>

          <FormField label="Kind" required error={errors.kind?.message ?? null} {...(fixedHint ? { hint: fixedHint } : {})}>
            <Select
              disabled={isEdit}
              {...register("kind", {
                // The required ledger account type follows the kind, so a
                // choice made for the other kind no longer fits.
                onChange: () => setValue("account_id", "", { shouldDirty: true }),
              })}
            >
              {(Object.keys(BANK_ACCOUNT_KIND_LABELS) as BankAccountKind[]).map((value) => (
                <option key={value} value={value}>
                  {BANK_ACCOUNT_KIND_LABELS[value]}
                </option>
              ))}
            </Select>
          </FormField>

          <FormField
            label="Ledger account"
            required
            error={errors.account_id?.message ?? null}
            hint={
              fixedHint ??
              (kind === "credit_card"
                ? "A liability account not linked to another bank account. A card is money owed."
                : "An asset account not linked to another bank account. Its balance is the book balance.")
            }
          >
            <Controller
              control={control}
              name="account_id"
              render={({ field }) => (
                <AccountPicker
                  accountType={kind === "credit_card" ? "liability" : "asset"}
                  value={field.value || null}
                  onChange={(value) => field.onChange(value ?? "")}
                  disabled={isEdit}
                />
              )}
            />
          </FormField>

          <FormField
            label="Currency"
            error={errors.currency_code?.message ?? null}
            hint={fixedHint ?? `Defaults to ${org.currency}. Transfers only move money between accounts in the same currency.`}
          >
            <Input className="uppercase tabular" maxLength={3} disabled={isEdit} {...register("currency_code")} />
          </FormField>
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="Bank details" />
        <CardBody className="grid gap-4 sm:grid-cols-2">
          <FormField label="Bank name" error={errors.bank_name?.message ?? null}>
            <Input {...register("bank_name")} />
          </FormField>

          <FormField
            label="Branch identifier"
            error={errors.branch_identifier?.message ?? null}
            hint="IFSC, sort code or routing number."
          >
            <Input className="tabular" {...register("branch_identifier")} />
          </FormField>

          <FormField
            label={isEdit ? "Replace account number" : "Account number"}
            error={errors.account_number?.message ?? null}
            hint={
              isEdit
                ? `Stored as ${bankAccount?.masked_number || "no digits"}. Leave blank to keep it. Only the last four digits of a new number are kept.`
                : "Only the last four digits are kept. The full number is never stored or shown again."
            }
          >
            <Input className="tabular" autoComplete="off" spellCheck={false} {...register("account_number")} />
          </FormField>

          {isEdit ? (
            <div className="flex items-end">
              <Checkbox
                label="Active"
                hint="Inactive accounts keep their history but cannot receive imports or transfers."
                {...register("is_active")}
              />
            </div>
          ) : null}
        </CardBody>
      </Card>

      <Card>
        <CardHeader
          title="Opening statement balance"
          description="What the bank statement showed on the day you start using EasyBook for this account. It is not the ledger's opening balance."
        />
        <CardBody className="grid gap-4 sm:grid-cols-2">
          <FormField
            label="Opening balance"
            error={errors.opening_balance?.message ?? null}
            disabled={openingBalanceLocked}
            hint={lockedHint ?? (kind === "credit_card" ? "An amount owed on a card is negative." : "Leave blank for zero.")}
          >
            <Controller
              control={control}
              name="opening_balance"
              render={({ field }) => (
                <NumericInput
                  value={field.value}
                  onValueChange={field.onChange}
                  onBlur={field.onBlur}
                  disabled={openingBalanceLocked}
                />
              )}
            />
          </FormField>

          <FormField
            label="Balance date"
            error={errors.opening_balance_date?.message ?? null}
            disabled={openingBalanceLocked}
            {...(lockedHint ? { hint: "Locked with the opening balance." } : { hint: "Statement lines on or before this date are left out of the statement balance." })}
          >
            <Input type="date" disabled={openingBalanceLocked} {...register("opening_balance_date")} />
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
        <Button type="submit" variant="primary" loading={mutation.isPending} loadingLabel="Saving">
          {isEdit ? "Save changes" : "Create bank account"}
        </Button>
      </div>
    </form>
  );
}
