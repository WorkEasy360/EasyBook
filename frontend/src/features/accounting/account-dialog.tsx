"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { Controller, useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Checkbox, FormError, FormField, Input, Select, Textarea } from "@/components/ui/field";
import { ACCOUNT_TYPE_LABELS } from "@/components/ui/status-badge";
import { useToast } from "@/components/ui/toast";
import { AccountPicker } from "@/features/shared/pickers";
import { useApiMutation } from "@/lib/hooks/use-api";
import { api } from "@/lib/api/browser";
import { fieldErrorsOf, formErrorOf, referenceOf } from "@/lib/api/errors";
import type { Account, AccountInput, AccountType } from "@/types/api/accounting";

/**
 * Create or edit a chart-of-accounts entry in a dialog.
 *
 * System accounts are ones the platform itself posts to, so
 * accounting/services/accounts.py freezes everything except name, subtype and
 * description (`system_account_protected`). Those controls are shown disabled
 * with the reason, and the PATCH carries only the mutable keys — sending an
 * unchanged `code` would still be refused.
 *
 * The parent is not restricted to the same account type: the service does not
 * enforce that, and a picker that did would be inventing a rule. It does
 * refuse a parent that would create a cycle (`account_hierarchy_cycle`),
 * which arrives as the form-level error.
 */

const TYPES = Object.keys(ACCOUNT_TYPE_LABELS) as [AccountType, ...AccountType[]];

const schema = z.object({
  code: z.string().trim().min(1, "A code is required.").max(32),
  name: z.string().trim().min(1, "A name is required.").max(255),
  account_type: z.enum(TYPES),
  account_subtype: z.string().trim().max(64),
  parent: z.string().nullable(),
  description: z.string().max(4000),
  is_active: z.boolean(),
});

type Values = z.infer<typeof schema>;

function defaultsOf(account: Account | undefined): Values {
  return {
    code: account?.code ?? "",
    name: account?.name ?? "",
    account_type: account?.account_type ?? "asset",
    account_subtype: account?.account_subtype ?? "",
    parent: account?.parent ?? null,
    description: account?.description ?? "",
    is_active: account?.is_active ?? true,
  };
}

export function AccountDialogButton({
  account,
  triggerVariant,
}: {
  account?: Account;
  /** Defaults to a link-style "Edit" for a row and a primary "New account" otherwise. */
  triggerVariant?: "primary" | "secondary" | "link";
}) {
  const router = useRouter();
  const toast = useToast();
  const [open, setOpen] = React.useState(false);
  const [formError, setFormError] = React.useState<{ message: string | null; reference: string | null }>({
    message: null,
    reference: null,
  });
  const isSystem = Boolean(account?.is_system);
  const formId = React.useId();

  const {
    control,
    register,
    handleSubmit,
    reset,
    setError,
    formState: { errors },
  } = useForm<Values>({ resolver: zodResolver(schema), defaultValues: defaultsOf(account) });

  const mutation = useApiMutation<Account, AccountInput>(
    "accounting/accounts",
    (input) =>
      account
        ? api.patch<Account>(`accounting/accounts/${account.id}`, input)
        : api.post<Account>("accounting/accounts", input),
    {
      onSuccess: (saved) => {
        toast.push({
          tone: "success",
          title: account ? "Account updated" : "Account created",
          description: `${saved.code} · ${saved.name}`,
        });
        setOpen(false);
        reset(defaultsOf(account ? saved : undefined));
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
    const editable = {
      name: values.name,
      account_subtype: values.account_subtype,
      description: values.description,
    };
    mutation.mutate(
      isSystem
        ? editable
        : {
            ...editable,
            code: values.code,
            account_type: values.account_type,
            parent: values.parent,
            // A new account is always created active (the model default);
            // deactivating is an edit. Also avoids a backend bug, fixed in
            // accounting/services/accounts.py but live only after a restart,
            // where sending is_active on create raised a 500.
            ...(account ? { is_active: values.is_active } : {}),
          },
    );
  }

  const lockedHint = isSystem ? "Fixed on a system account — the platform posts to it." : undefined;
  const label = account ? "Edit" : "New account";
  const variant = triggerVariant ?? (account ? "link" : "primary");

  return (
    <>
      <Button
        variant={variant}
        size={variant === "link" ? "sm" : "md"}
        onClick={() => {
          setFormError({ message: null, reference: null });
          reset(defaultsOf(account));
          setOpen(true);
        }}
      >
        {label}
        {account ? <span className="sr-only"> account {account.code}</span> : null}
      </Button>

      <Dialog
        open={open}
        onClose={() => {
          if (!mutation.isPending) setOpen(false);
        }}
        title={account ? `Edit ${account.code} · ${account.name}` : "New account"}
        {...(isSystem ? { description: "A system account: only its name, subtype and description can change." } : {})}
        dismissible={!mutation.isPending}
        size="lg"
        footer={
          <>
            <Button variant="secondary" onClick={() => setOpen(false)} disabled={mutation.isPending}>
              Cancel
            </Button>
            <Button type="submit" form={formId} variant="primary" loading={mutation.isPending}>
              {account ? "Save changes" : "Create account"}
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
              hint={lockedHint ?? "Unique in this organization, e.g. 5210."}
              disabled={isSystem}
            >
              <Input className="tabular" {...register("code")} />
            </FormField>
            <FormField label="Name" required error={errors.name?.message ?? null}>
              <Input {...register("name")} />
            </FormField>
            <FormField
              label="Type"
              required
              error={errors.account_type?.message ?? null}
              disabled={isSystem}
              hint={
                lockedHint ??
                (account
                  ? "Changing the type moves this account's history to another section of the reports."
                  : "Decides where the account appears in the financial statements.")
              }
            >
              <Select {...register("account_type")}>
                {TYPES.map((type) => (
                  <option key={type} value={type}>
                    {ACCOUNT_TYPE_LABELS[type]}
                  </option>
                ))}
              </Select>
            </FormField>
            <FormField
              label="Subtype"
              error={errors.account_subtype?.message ?? null}
              hint="Free text used for grouping, e.g. bank, receivable."
            >
              <Input {...register("account_subtype")} />
            </FormField>
            <FormField
              label="Parent account"
              error={errors.parent?.message ?? null}
              disabled={isSystem}
              className="sm:col-span-2"
              hint={lockedHint ?? "Optional. Groups this account under another in the chart."}
            >
              <Controller
                control={control}
                name="parent"
                render={({ field }) => (
                  <AccountPicker value={field.value} onChange={field.onChange} disabled={isSystem} placeholder="No parent" />
                )}
              />
            </FormField>
            <FormField label="Description" error={errors.description?.message ?? null} className="sm:col-span-2">
              <Textarea rows={2} {...register("description")} />
            </FormField>
          </div>
          {account ? (
            <Checkbox
              label="Active"
              disabled={isSystem}
              hint={
                isSystem
                  ? "System accounts cannot be deactivated."
                  : "Inactive accounts keep their history but cannot receive new postings."
              }
              {...register("is_active")}
            />
          ) : null}
        </form>
      </Dialog>
    </>
  );
}
