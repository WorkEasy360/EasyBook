"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { Controller, useForm, useWatch } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Checkbox, FormError, FormField, Input, Select } from "@/components/ui/field";
import { NumericInput } from "@/components/ui/numeric-input";
import { useToast } from "@/components/ui/toast";
import { AccountPicker } from "@/features/shared/pickers";
import { useUnsavedChanges } from "@/lib/hooks/use-unsaved-changes";
import { useIdempotentMutation } from "@/lib/hooks/use-api";
import { api } from "@/lib/api/browser";
import { errorCodeOf, fieldErrorsOf, formErrorOf, referenceOf } from "@/lib/api/errors";
import { compare, isValidDecimal } from "@/lib/money";
import { BankAccountPicker } from "./bank-account-picker";
import {
  RULE_ACTION_LABELS,
  RULE_DIRECTION_LABELS,
  type BankRule,
  type BankRuleInput,
  type BankRuleUpdateInput,
  type RuleAction,
  type RuleDirection,
} from "@/types/api/banking";

/**
 * Create or edit a bank rule (banking/services/rules.py).
 *
 * Rules are deliberately simple: every filled-in condition must hold, text
 * conditions are case-insensitive "contains" (never a regex), amounts compare
 * the absolute value, and the FIRST active rule by priority decides — rules
 * do not combine. The form enforces the service's refusals up front:
 * at least one condition (rule_has_no_conditions), a target account for a
 * categorize rule and none for an exclude rule, and a range that is not
 * backwards.
 *
 * `auto_confirm` is off by default and stays a conscious choice: with it on,
 * a matching line is categorized and its journal posted with nobody looking.
 * The scope (one bank account or all) is fixed at creation —
 * BankRuleUpdateSerializer does not accept it.
 */

const DIRECTIONS = Object.keys(RULE_DIRECTION_LABELS) as [RuleDirection, ...RuleDirection[]];
const ACTIONS = Object.keys(RULE_ACTION_LABELS) as [RuleAction, ...RuleAction[]];
const optionalAmount = z
  .string()
  .refine((value) => value === "" || (isValidDecimal(value) && compare(value, "0") >= 0), "Enter zero or more, or leave blank.");

const schema = z
  .object({
    name: z.string().trim().min(1, "Give the rule a name.").max(255),
    priority: z.string().regex(/^\d+$/, "Enter a whole number, 0 or more."),
    is_active: z.boolean(),
    bank_account_id: z.string().nullable(),
    description_contains: z.string().max(255),
    counterparty_contains: z.string().max(255),
    direction: z.enum(DIRECTIONS),
    amount_min: optionalAmount,
    amount_max: optionalAmount,
    action: z.enum(ACTIONS),
    target_account_id: z.string().nullable(),
    auto_confirm: z.boolean(),
  })
  .refine(
    (values) =>
      values.description_contains.trim() !== "" ||
      values.counterparty_contains.trim() !== "" ||
      values.amount_min !== "" ||
      values.amount_max !== "" ||
      values.direction !== "any",
    // A rule with no conditions would match every line on import.
    { message: "Add at least one condition — a rule without one would match every line.", path: ["description_contains"] },
  )
  .refine(
    (values) =>
      values.amount_min === "" ||
      values.amount_max === "" ||
      !isValidDecimal(values.amount_min) ||
      !isValidDecimal(values.amount_max) ||
      compare(values.amount_max, values.amount_min) >= 0,
    { message: "The maximum cannot be less than the minimum.", path: ["amount_max"] },
  )
  .refine((values) => values.action !== "categorize" || Boolean(values.target_account_id), {
    message: "Choose the account to categorize to.",
    path: ["target_account_id"],
  });

type Values = z.infer<typeof schema>;

const CODE_FIELDS: Record<string, keyof Values> = {
  rule_has_no_conditions: "description_contains",
  rule_target_account_required: "target_account_id",
  rule_target_account_unexpected: "target_account_id",
  rule_name_taken: "name",
  rule_amount_range_invalid: "amount_max",
  rule_amount_min_negative: "amount_min",
};

function toValues(rule: BankRule | undefined): Values {
  return {
    name: rule?.name ?? "",
    priority: String(rule?.priority ?? 100),
    is_active: rule?.is_active ?? true,
    bank_account_id: rule?.bank_account ?? null,
    description_contains: rule?.description_contains ?? "",
    counterparty_contains: rule?.counterparty_contains ?? "",
    direction: rule?.direction ?? "any",
    amount_min: rule?.amount_min ?? "",
    amount_max: rule?.amount_max ?? "",
    action: rule?.action ?? "categorize",
    target_account_id: rule?.target_account ?? null,
    auto_confirm: rule?.auto_confirm ?? false,
  };
}

export function RuleForm({ rule }: { rule?: BankRule }) {
  const router = useRouter();
  const toast = useToast();
  const isEdit = Boolean(rule);
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
  } = useForm<Values>({ resolver: zodResolver(schema), defaultValues: toValues(rule) });
  useUnsavedChanges(isDirty && !isSubmitting);

  const action = useWatch({ control, name: "action" });
  const autoConfirm = useWatch({ control, name: "auto_confirm" });

  const mutation = useIdempotentMutation<BankRule, BankRuleInput | BankRuleUpdateInput>(
    "bank-rules",
    (input, idempotencyKey) =>
      rule ? api.patch<BankRule>(`bank-rules/${rule.id}`, input) : api.post<BankRule>("bank-rules", input, { idempotencyKey }),
    {
      onSuccess: (saved) => {
        toast.push({ tone: "success", title: isEdit ? "Rule saved" : "Rule created", description: saved.name });
        router.push("/banking/rules");
        router.refresh();
      },
      onError: (error) => {
        setFormError({ message: formErrorOf(error), reference: referenceOf(error) });
        for (const [field, messages] of Object.entries(fieldErrorsOf(error))) {
          if (field in toValues(undefined) && messages[0]) setError(field as keyof Values, { type: "server", message: messages[0] });
        }
        const code = errorCodeOf(error);
        const field = code ? CODE_FIELDS[code] : undefined;
        if (field) setError(field, { type: "server", message: error.message });
      },
    },
  );

  function onSubmit(values: Values) {
    setFormError({ message: null, reference: null });
    const common = {
      name: values.name.trim(),
      priority: Number.parseInt(values.priority, 10),
      description_contains: values.description_contains.trim(),
      counterparty_contains: values.counterparty_contains.trim(),
      direction: values.direction,
      amount_min: values.amount_min === "" ? null : values.amount_min,
      amount_max: values.amount_max === "" ? null : values.amount_max,
      action: values.action,
      // An exclude rule must not carry a target (rule_target_account_unexpected).
      target_account_id: values.action === "categorize" ? values.target_account_id : null,
      auto_confirm: values.action === "categorize" ? values.auto_confirm : false,
    };
    if (rule) {
      mutation.mutate({ ...common, is_active: values.is_active });
      return;
    }
    mutation.mutate({ ...common, bank_account_id: values.bank_account_id });
  }

  const conditionsError = errors.description_contains?.message;

  return (
    <form method="post" noValidate onSubmit={(event) => void handleSubmit(onSubmit)(event)} className="flex flex-col gap-4">
      <FormError message={formError.message} reference={formError.reference} />

      <Card>
        <CardHeader title="Rule" />
        <CardBody className="grid gap-4 sm:grid-cols-2">
          <FormField label="Name" required error={errors.name?.message ?? null} hint="Unique within your organization.">
            <Input {...register("name")} />
          </FormField>
          <FormField
            label="Priority"
            required
            error={errors.priority?.message ?? null}
            hint="Lower numbers run first. The first matching rule decides."
          >
            <Input inputMode="numeric" className="tabular" {...register("priority")} />
          </FormField>
          <FormField
            label="Bank account"
            hint={isEdit ? "Fixed once the rule exists." : "Leave empty to apply the rule to every bank account."}
          >
            <Controller
              control={control}
              name="bank_account_id"
              render={({ field }) => (
                <BankAccountPicker activeOnly={false} value={field.value} onChange={field.onChange} disabled={isEdit} placeholder="All bank accounts" />
              )}
            />
          </FormField>
          {isEdit ? (
            <div className="flex items-end">
              <Checkbox label="Active" hint="Inactive rules are skipped." {...register("is_active")} />
            </div>
          ) : null}
        </CardBody>
      </Card>

      <Card>
        <CardHeader
          title="When a statement line…"
          description="Every condition you fill in must hold. Text matches ignore case; amounts compare the size of the amount, whatever its direction."
        />
        <CardBody className="grid gap-4 sm:grid-cols-2">
          {conditionsError ? (
            <p role="alert" className="text-xs text-danger-600 sm:col-span-2">
              {conditionsError}
            </p>
          ) : null}
          <FormField label="Description or reference contains">
            <Input {...register("description_contains")} />
          </FormField>
          <FormField label="Counterparty contains" error={errors.counterparty_contains?.message ?? null}>
            <Input {...register("counterparty_contains")} />
          </FormField>
          <FormField label="Direction" required error={errors.direction?.message ?? null}>
            <Select {...register("direction")}>
              {DIRECTIONS.map((value) => (
                <option key={value} value={value}>
                  {RULE_DIRECTION_LABELS[value]}
                </option>
              ))}
            </Select>
          </FormField>
          <div className="grid gap-4 sm:grid-cols-2">
            <FormField label="Amount at least" error={errors.amount_min?.message ?? null}>
              <Controller
                control={control}
                name="amount_min"
                render={({ field }) => (
                  <NumericInput nonNegative value={field.value} onValueChange={field.onChange} onBlur={field.onBlur} />
                )}
              />
            </FormField>
            <FormField label="Amount at most" error={errors.amount_max?.message ?? null}>
              <Controller
                control={control}
                name="amount_max"
                render={({ field }) => (
                  <NumericInput nonNegative value={field.value} onValueChange={field.onChange} onBlur={field.onBlur} />
                )}
              />
            </FormField>
          </div>
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="…then" />
        <CardBody className="grid gap-4 sm:grid-cols-2">
          <FormField label="Action" required error={errors.action?.message ?? null}>
            <Select
              {...register("action", {
                onChange: (event: React.ChangeEvent<HTMLSelectElement>) => {
                  if (event.target.value === "exclude") {
                    setValue("target_account_id", null, { shouldDirty: true });
                    setValue("auto_confirm", false, { shouldDirty: true });
                  }
                },
              })}
            >
              {ACTIONS.map((value) => (
                <option key={value} value={value}>
                  {RULE_ACTION_LABELS[value]}
                </option>
              ))}
            </Select>
          </FormField>

          {action === "categorize" ? (
            <FormField
              label="Categorize to"
              required
              error={errors.target_account_id?.message ?? null}
              hint="The account the journal posts against."
            >
              <Controller
                control={control}
                name="target_account_id"
                render={({ field }) => <AccountPicker value={field.value} onChange={field.onChange} />}
              />
            </FormField>
          ) : (
            <p className="self-end text-xs text-ink-500 sm:pb-2">
              Excluding is only right for lines that are not real transactions, such as bank artefacts. It posts nothing.
            </p>
          )}

          {action === "categorize" ? (
            <div className="sm:col-span-2">
              <Checkbox
                label="Post automatically, without review"
                hint={
                  autoConfirm
                    ? "On: a matching line is categorized and its journal posted as soon as rules run, with nobody checking it."
                    : "Off: a matching rule only tells you it matched; you categorize the line yourself."
                }
                {...register("auto_confirm")}
              />
            </div>
          ) : null}
        </CardBody>
      </Card>

      <div className="flex items-center justify-end gap-2">
        <Button variant="secondary" onClick={() => router.back()} disabled={mutation.isPending}>
          Cancel
        </Button>
        <Button type="submit" variant="primary" loading={mutation.isPending} loadingLabel="Saving">
          {isEdit ? "Save rule" : "Create rule"}
        </Button>
      </div>
    </form>
  );
}
