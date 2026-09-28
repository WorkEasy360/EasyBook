"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { Controller, useFieldArray, useForm, useWatch, type Control, type FieldErrors, type UseFormRegister, type UseFormSetValue } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { Button, IconButton } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Checkbox, FormError, FormField, Input, Select, Textarea } from "@/components/ui/field";
import { Icons } from "@/components/ui/icons";
import { NumericInput } from "@/components/ui/numeric-input";
import { useToast } from "@/components/ui/toast";
import { CustomerPicker, ItemPicker, VendorPicker } from "@/features/shared/pickers";
import { useUnsavedChanges } from "@/lib/hooks/use-unsaved-changes";
import { useApiMutation } from "@/lib/hooks/use-api";
import { api } from "@/lib/api/browser";
import { fieldErrorsOf, formErrorOf, referenceOf } from "@/lib/api/errors";
import {
  TRIGGER_CATEGORY_LABELS,
  type ActionCatalogEntry,
  type AutomationRule,
  type AutomationRuleInput,
  type AutomationRuleUpdateInput,
  type TriggerCatalogEntry,
} from "@/types/api/automation";
import {
  COOLDOWN_TRIGGERS,
  NO_VALUE_OPERATORS,
  OPERATOR_LABELS,
  WEBHOOK_ACTION_ID,
  actionAllowedForTrigger,
  conditionFieldsFor,
  conditionValueError,
  configFieldSpec,
  operatorAllowed,
  operatorsFor,
} from "./contract";
import {
  EMPTY_CONDITION,
  actionsChanged,
  buildActions,
  buildConditions,
  conditionsChanged,
  emptyActionRow,
  ruleSettingsFrom,
  toActionRows,
  toConditionRows,
  toOptionalCount,
  type ActionRow,
} from "./rule-payload";

/**
 * Create or edit an automation rule, built entirely from the live catalogs.
 *
 * Saving never runs anything: a new rule is a DRAFT, and only an ACTIVE rule
 * executes (services/rules.py). Activation is a separate, confirmed action on
 * the rule page, where the backend re-validates the whole definition.
 *
 * Edits (AutomationRuleDetailView.update): the trigger is fixed after
 * creation — the update serializer has no `trigger_type` — so it is shown
 * disabled. Conditions and actions are sent only when they changed, because
 * sending either replaces the list and bumps the rule's version.
 */

const WHOLE = /^\d+$/;

export interface MemberOption {
  /** User id — what send_notification's recipient_id holds. */
  id: string;
  label: string;
}

const conditionRowSchema = z.object({ field: z.string(), operator: z.string(), value: z.string() });
const actionRowSchema = z.object({
  action_id: z.string(),
  config: z.record(z.string(), z.string()),
  secretConfigured: z.boolean(),
  removeSecret: z.boolean(),
});

const baseSchema = z.object({
  name: z.string().trim().min(1, "Enter a name.").max(255, "Keep the name under 255 characters."),
  description: z.string(),
  trigger_type: z.string().min(1, "Choose a trigger."),
  priority: z.string().trim().regex(WHOLE, "Enter a whole number, 0 or more."),
  stop_on_failure: z.boolean(),
  cooldown_days: z.string().trim().refine((value) => value === "" || WHOLE.test(value), "Enter a whole number of days."),
  max_runs_per_period: z
    .string()
    .trim()
    .refine((value) => value === "" || (WHOLE.test(value) && Number(value) >= 1), "Enter a whole number, 1 or more."),
  conditions: z.array(conditionRowSchema),
  actions: z.array(actionRowSchema),
});

export type RuleFormValues = z.infer<typeof baseSchema>;

function makeSchema(
  catalog: readonly ActionCatalogEntry[],
  rule: AutomationRule | undefined,
  canManageWebhooks: boolean,
) {
  return baseSchema.superRefine((values, ctx) => {
    const fields = conditionFieldsFor(values.trigger_type);

    values.conditions.forEach((row, index) => {
      if (row.field === "") {
        ctx.addIssue({ code: "custom", path: ["conditions", index, "field"], message: "Choose a field." });
        return;
      }
      if (fields && !Object.hasOwn(fields, row.field)) {
        ctx.addIssue({
          code: "custom",
          path: ["conditions", index, "field"],
          message: "This field is not available for the chosen trigger.",
        });
        return;
      }
      const type = fields?.[row.field]?.type;
      if (!operatorAllowed(type, row.operator)) {
        ctx.addIssue({ code: "custom", path: ["conditions", index, "operator"], message: "Choose a comparison." });
        return;
      }
      const valueError = conditionValueError(type, row.operator, row.value);
      if (valueError) ctx.addIssue({ code: "custom", path: ["conditions", index, "value"], message: valueError });
    });

    if (values.actions.length === 0) {
      // services/rules.py :: automation_rule_no_actions
      ctx.addIssue({ code: "custom", path: ["actions"], message: "Add at least one action." });
    }
    values.actions.forEach((row, index) => {
      const entry = catalog.find((action) => action.id === row.action_id);
      if (!entry) return; // reported by buildActions below
      if (values.trigger_type && !actionAllowedForTrigger(entry, values.trigger_type)) {
        ctx.addIssue({
          code: "custom",
          path: ["actions", index, "action_id"],
          message: "This action cannot be used with the chosen trigger.",
        });
      }
      if (entry.id === WEBHOOK_ACTION_ID && !canManageWebhooks) {
        ctx.addIssue({
          code: "custom",
          path: ["actions", index, "action_id"],
          message: "Only owners and admins can configure webhooks.",
        });
      }
    });

    const requireSecretDecision = rule ? actionsChanged(rule.actions, values.actions, catalog) : false;
    for (const issue of buildActions(values.actions, catalog, { requireSecretDecision }).issues) {
      ctx.addIssue({
        code: "custom",
        path: issue.key === null ? ["actions", issue.index, "action_id"] : ["actions", issue.index, "config", issue.key],
        message: issue.message,
      });
    }
  });
}

const SERVER_FIELDS = new Set(["name", "description", "trigger_type", "priority", "cooldown_days", "max_runs_per_period"]);

export function RuleForm({
  rule,
  triggers,
  catalog,
  members,
  canManageWebhooks,
}: {
  rule?: AutomationRule;
  triggers: TriggerCatalogEntry[];
  catalog: ActionCatalogEntry[];
  members: MemberOption[];
  canManageWebhooks: boolean;
}) {
  const router = useRouter();
  const toast = useToast();
  const isEdit = Boolean(rule);
  const [formError, setFormError] = React.useState<{ message: string | null; reference: string | null }>({
    message: null,
    reference: null,
  });

  const schema = React.useMemo(() => makeSchema(catalog, rule, canManageWebhooks), [catalog, rule, canManageWebhooks]);

  const form = useForm<RuleFormValues>({
    resolver: zodResolver(schema),
    defaultValues: {
      name: rule?.name ?? "",
      description: rule?.description ?? "",
      trigger_type: rule?.trigger_type ?? "",
      stop_on_failure: rule?.stop_on_failure ?? false,
      ...ruleSettingsFrom(rule),
      conditions: rule ? toConditionRows(rule.conditions) : [],
      actions: rule ? toActionRows(rule.actions) : [emptyActionRow()],
    },
  });
  const {
    control,
    register,
    handleSubmit,
    setValue,
    setError,
    formState: { errors, isDirty, isSubmitting },
  } = form;

  useUnsavedChanges(isDirty && !isSubmitting);

  const triggerType = useWatch({ control, name: "trigger_type" });
  const conditions = useFieldArray({ control, name: "conditions" });
  const actions = useFieldArray({ control, name: "actions" });

  // Changing a webhook rule's conditions or actions needs manage_webhooks
  // even for an editor (services/rules.py :: _assert_actions_authorized runs
  // on any structural change), so those sections are locked instead of
  // offered and then refused.
  const structureLocked =
    !canManageWebhooks && Boolean(rule?.actions.some((action) => action.action_id === WEBHOOK_ACTION_ID));

  const mutation = useApiMutation<AutomationRule, AutomationRuleInput | AutomationRuleUpdateInput>(
    "automation/rules",
    // automation views do not implement IdempotentCreateMixin, so no
    // Idempotency-Key is sent; the pending button is the double-submit guard.
    (input) =>
      rule
        ? api.patch<AutomationRule>(`automation/rules/${rule.id}`, input)
        : api.post<AutomationRule>("automation/rules", input),
    {
      onSuccess: (saved) => {
        toast.push({ tone: "success", title: isEdit ? "Rule saved" : "Draft rule created", description: saved.name });
        router.push(`/automation/rules/${saved.id}`);
        router.refresh();
      },
      onError: (error) => {
        setFormError({ message: formErrorOf(error), reference: referenceOf(error) });
        for (const [field, messages] of Object.entries(fieldErrorsOf(error))) {
          if (SERVER_FIELDS.has(field) && messages[0]) {
            setError(field as keyof RuleFormValues, { type: "server", message: messages[0] });
          }
        }
      },
    },
  );

  function onSubmit(values: RuleFormValues) {
    setFormError({ message: null, reference: null });
    const settings = {
      name: values.name.trim(),
      description: values.description,
      priority: Number(values.priority.trim()),
      stop_on_failure: values.stop_on_failure,
      cooldown_days: toOptionalCount(values.cooldown_days),
      max_runs_per_period: toOptionalCount(values.max_runs_per_period),
    };
    const builtConditions = buildConditions(values.conditions);

    if (rule) {
      const sendActions = actionsChanged(rule.actions, values.actions, catalog);
      const sendConditions = conditionsChanged(rule.conditions, builtConditions);
      const { actions: builtActions } = buildActions(values.actions, catalog, { requireSecretDecision: sendActions });
      mutation.mutate({
        ...settings,
        ...(sendConditions ? { conditions: builtConditions } : {}),
        ...(sendActions ? { actions: builtActions } : {}),
      });
      return;
    }

    const { actions: builtActions } = buildActions(values.actions, catalog, { requireSecretDecision: false });
    mutation.mutate({
      ...settings,
      trigger_type: values.trigger_type,
      conditions: builtConditions,
      actions: builtActions,
    });
  }

  const categories = ["event", "schedule", "manual"];
  const knownFields = conditionFieldsFor(triggerType);
  const fieldEntries = knownFields ? Object.entries(knownFields) : [];
  const conditionsUnavailable = Boolean(triggerType) && knownFields !== undefined && fieldEntries.length === 0;

  return (
    <form method="post" noValidate onSubmit={(event) => void handleSubmit(onSubmit)(event)} className="flex flex-col gap-4">
      <FormError message={formError.message} reference={formError.reference} />

      {rule?.status === "active" ? (
        <p role="note" className="rounded-md border border-info-100 bg-info-50 px-3 py-2 text-sm text-info-700">
          This rule is active. Saved changes apply to its next run. Changing conditions or actions saves a new version;
          past runs keep the version they ran.
        </p>
      ) : null}

      <Card>
        <CardHeader title="Rule" />
        <CardBody className="grid gap-4 sm:grid-cols-2">
          <FormField label="Name" required error={errors.name?.message ?? null}>
            <Input {...register("name")} autoComplete="off" />
          </FormField>

          <FormField
            label="Trigger"
            required
            error={errors.trigger_type?.message ?? null}
            hint={isEdit ? "Fixed once the rule is created." : "What starts the rule."}
          >
            <Select
              {...register("trigger_type", {
                onChange: () => {
                  // Condition fields belong to one trigger (schemas.py ::
                  // TRIGGER_FIELDS), so conditions written for the previous
                  // trigger cannot carry over.
                  conditions.replace([]);
                },
              })}
              disabled={isEdit}
              placeholder="Choose a trigger"
            >
              {categories.map((category) => {
                const group = triggers.filter((trigger) => trigger.category === category);
                return group.length > 0 ? (
                  <optgroup key={category} label={TRIGGER_CATEGORY_LABELS[category] ?? category}>
                    {group.map((trigger) => (
                      <option key={trigger.id} value={trigger.id}>
                        {trigger.label}
                      </option>
                    ))}
                  </optgroup>
                ) : null;
              })}
              {triggers
                .filter((trigger) => !categories.includes(trigger.category))
                .map((trigger) => (
                  <option key={trigger.id} value={trigger.id}>
                    {trigger.label}
                  </option>
                ))}
              {rule && !triggers.some((trigger) => trigger.id === rule.trigger_type) ? (
                <option value={rule.trigger_type}>{rule.trigger_type}</option>
              ) : null}
            </Select>
          </FormField>

          <FormField label="Description" error={errors.description?.message ?? null} className="sm:col-span-2">
            <Textarea rows={2} {...register("description")} />
          </FormField>
        </CardBody>
      </Card>

      <Card>
        <CardHeader
          title="Conditions"
          description="Every condition must match for the actions to run. With none, the rule runs on every trigger."
          actions={
            triggerType && !conditionsUnavailable && !structureLocked ? (
              <Button
                variant="secondary"
                size="sm"
                leadingIcon={<Icons.plus className="size-3.5" />}
                onClick={() => conditions.append({ ...EMPTY_CONDITION })}
              >
                Add condition
              </Button>
            ) : null
          }
        />
        <CardBody className="flex flex-col gap-3">
          {structureLocked ? <LockedNote /> : null}
          {!triggerType ? (
            <p className="text-sm text-ink-500">Choose a trigger to see the fields it can be filtered on.</p>
          ) : conditionsUnavailable ? (
            <p className="text-sm text-ink-500">This trigger has no record fields to filter on.</p>
          ) : conditions.fields.length === 0 ? (
            <p className="text-sm text-ink-500">No conditions.</p>
          ) : null}

          {conditions.fields.map((row, index) => (
            <ConditionEditor
              key={row.id}
              index={index}
              control={control}
              register={register}
              setValue={setValue}
              errors={errors}
              triggerType={triggerType}
              disabled={structureLocked}
              onRemove={() => conditions.remove(index)}
            />
          ))}
        </CardBody>
      </Card>

      <Card>
        <CardHeader
          title="Actions"
          description="Run in this order. Automation actions never post to the ledger, move stock or send money."
          actions={
            structureLocked ? null : (
              <Button
                variant="secondary"
                size="sm"
                leadingIcon={<Icons.plus className="size-3.5" />}
                onClick={() => actions.append(emptyActionRow())}
              >
                Add action
              </Button>
            )
          }
        />
        <CardBody className="flex flex-col gap-3">
          {structureLocked ? <LockedNote /> : null}
          {errors.actions?.root?.message || errors.actions?.message ? (
            <p role="alert" className="text-xs text-danger-600">
              {errors.actions?.root?.message ?? errors.actions?.message}
            </p>
          ) : null}

          {actions.fields.map((row, index) => (
            <ActionEditor
              key={row.id}
              index={index}
              count={actions.fields.length}
              control={control}
              register={register}
              setValue={setValue}
              errors={errors}
              triggerType={triggerType}
              catalog={catalog}
              members={members}
              canManageWebhooks={canManageWebhooks}
              disabled={structureLocked}
              onRemove={() => actions.remove(index)}
              onMove={(to) => actions.move(index, to)}
            />
          ))}
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="Run limits" />
        <CardBody className="grid gap-4 sm:grid-cols-3">
          <FormField
            label="Priority"
            required
            error={errors.priority?.message ?? null}
            hint="Higher numbers are listed and evaluated first."
          >
            <Input inputMode="numeric" className="tabular" {...register("priority")} />
          </FormField>
          <FormField
            label="Most runs per 24 hours"
            error={errors.max_runs_per_period?.message ?? null}
            hint="Blank means no limit. Counts every run in a rolling 24 hours."
          >
            <Input inputMode="numeric" className="tabular" {...register("max_runs_per_period")} />
          </FormField>
          <FormField
            label="Cooldown (days)"
            error={errors.cooldown_days?.message ?? null}
            hint={
              COOLDOWN_TRIGGERS.has(triggerType)
                ? "Minimum days before the same record triggers this rule again. Blank uses the default."
                : "Only used by triggers that scan for records, such as Invoice overdue."
            }
          >
            <Input inputMode="numeric" className="tabular" {...register("cooldown_days")} />
          </FormField>
          <Checkbox
            className="sm:col-span-3"
            label="Stop at the first failed action"
            hint="Otherwise later actions still run after one fails."
            {...register("stop_on_failure")}
          />
        </CardBody>
      </Card>

      <div className="flex items-center justify-end gap-2">
        <Button variant="secondary" onClick={() => router.back()} disabled={mutation.isPending}>
          Cancel
        </Button>
        <Button type="submit" variant="primary" loading={mutation.isPending} loadingLabel="Saving">
          {isEdit ? "Save rule" : "Save as draft"}
        </Button>
      </div>
    </form>
  );
}

function LockedNote() {
  return (
    <p role="note" className="text-sm text-ink-500">
      This rule calls a webhook, so its conditions and actions can only be changed by an owner or admin.
    </p>
  );
}

interface RowProps {
  index: number;
  control: Control<RuleFormValues>;
  register: UseFormRegister<RuleFormValues>;
  setValue: UseFormSetValue<RuleFormValues>;
  errors: FieldErrors<RuleFormValues>;
  triggerType: string;
  disabled: boolean;
  onRemove: () => void;
}

function ConditionEditor({ index, control, register, setValue, errors, triggerType, disabled, onRemove }: RowProps) {
  const field = useWatch({ control, name: `conditions.${index}.field` });
  const operator = useWatch({ control, name: `conditions.${index}.operator` });
  const rowErrors = errors.conditions?.[index];

  const known = conditionFieldsFor(triggerType);
  const spec = known?.[field];
  const operators = operatorsFor(spec?.type);
  const takesValue = operator !== "" && !NO_VALUE_OPERATORS.has(operator);

  return (
    <fieldset className="grid gap-3 rounded-md border border-ink-200 p-3 sm:grid-cols-12" disabled={disabled}>
      <legend className="sr-only">Condition {index + 1}</legend>

      <FormField label="Field" required error={rowErrors?.field?.message ?? null} className="sm:col-span-4">
        {known ? (
          <Select
            {...register(`conditions.${index}.field`, {
              onChange: (event: React.ChangeEvent<HTMLSelectElement>) => {
                // A comparison valid for one field type may not be for another.
                const nextType = known[event.target.value]?.type;
                if (!operatorAllowed(nextType, operator)) setValue(`conditions.${index}.operator`, "");
                setValue(`conditions.${index}.value`, "");
              },
            })}
            placeholder="Choose a field"
          >
            {Object.entries(known).map(([name, fieldSpec]) => (
              <option key={name} value={name}>
                {fieldSpec.label}
              </option>
            ))}
            {field && !Object.hasOwn(known, field) ? <option value={field}>{field}</option> : null}
          </Select>
        ) : (
          // A trigger newer than this build: the backend's allowlist decides.
          <Input {...register(`conditions.${index}.field`)} autoComplete="off" />
        )}
      </FormField>

      <FormField label="Comparison" required error={rowErrors?.operator?.message ?? null} className="sm:col-span-3">
        <Select {...register(`conditions.${index}.operator`)} placeholder="Choose">
          {operators.map((name) => (
            <option key={name} value={name}>
              {OPERATOR_LABELS[name]}
            </option>
          ))}
        </Select>
      </FormField>

      <FormField
        label="Value"
        required={takesValue}
        error={rowErrors?.value?.message ?? null}
        className="sm:col-span-4"
        {...(operator === "in"
          ? { hint: "Separate values with commas." }
          : spec?.hint && takesValue
            ? { hint: spec.hint }
            : {})}
      >
        <Controller
          control={control}
          name={`conditions.${index}.value`}
          render={({ field: value }) => {
            if (!takesValue) {
              return <Input value="" disabled placeholder={operator ? "No value needed" : ""} readOnly />;
            }
            if (spec?.picker && (operator === "equals" || operator === "not_equals")) {
              const pickerProps = {
                value: value.value || null,
                onChange: (next: string | null) => value.onChange(next ?? ""),
                disabled,
              };
              if (spec.picker === "customer") return <CustomerPicker {...pickerProps} />;
              if (spec.picker === "vendor") return <VendorPicker {...pickerProps} />;
              return <ItemPicker {...pickerProps} />;
            }
            if (spec?.type === "decimal") {
              return (
                <NumericInput
                  value={value.value}
                  onValueChange={value.onChange}
                  onBlur={value.onBlur}
                  scale={spec.money ? 2 : 4}
                  {...(spec.money ? {} : { currency: false as const })}
                />
              );
            }
            return (
              <Input
                value={value.value}
                onChange={value.onChange}
                onBlur={value.onBlur}
                inputMode={spec?.type === "int" ? "numeric" : undefined}
                autoComplete="off"
              />
            );
          }}
        />
      </FormField>

      <div className="flex items-end justify-end sm:col-span-1">
        <IconButton label={`Remove condition ${index + 1}`} icon={<Icons.close className="size-4" />} onClick={onRemove} />
      </div>
    </fieldset>
  );
}

function ActionEditor({
  index,
  count,
  control,
  register,
  setValue,
  errors,
  triggerType,
  catalog,
  members,
  canManageWebhooks,
  disabled,
  onRemove,
  onMove,
}: RowProps & {
  count: number;
  catalog: ActionCatalogEntry[];
  members: MemberOption[];
  canManageWebhooks: boolean;
  onMove: (to: number) => void;
}) {
  const actionId = useWatch({ control, name: `actions.${index}.action_id` });
  const row = useWatch({ control, name: `actions.${index}` }) as ActionRow | undefined;
  const rowErrors = errors.actions?.[index];
  const entry = catalog.find((action) => action.id === actionId);

  const offered = catalog.filter(
    (action) =>
      action.id === actionId ||
      ((!triggerType || actionAllowedForTrigger(action, triggerType)) &&
        (action.id !== WEBHOOK_ACTION_ID || canManageWebhooks)),
  );

  return (
    <fieldset className="grid gap-3 rounded-md border border-ink-200 p-3 sm:grid-cols-12" disabled={disabled}>
      <legend className="sr-only">Action {index + 1}</legend>

      <FormField
        label="Action"
        required
        error={rowErrors?.action_id?.message ?? null}
        className="sm:col-span-8"
        {...(entry ? { hint: `Safety level ${entry.safety_level}.` } : {})}
      >
        <Select
          {...register(`actions.${index}.action_id`, {
            onChange: () => {
              // Config keys belong to one action; never carry them across.
              setValue(`actions.${index}.config`, {});
              setValue(`actions.${index}.secretConfigured`, false);
              setValue(`actions.${index}.removeSecret`, false);
            },
          })}
          placeholder="Choose an action"
        >
          {offered.map((action) => (
            <option key={action.id} value={action.id}>
              {action.label}
            </option>
          ))}
          {actionId && !entry ? <option value={actionId}>{actionId}</option> : null}
        </Select>
      </FormField>

      <div className="flex items-end justify-end gap-1 sm:col-span-4">
        <Button variant="ghost" size="sm" disabled={index === 0} onClick={() => onMove(index - 1)}>
          Up<span className="sr-only"> (move action {index + 1} earlier)</span>
        </Button>
        <Button variant="ghost" size="sm" disabled={index === count - 1} onClick={() => onMove(index + 1)}>
          Down<span className="sr-only"> (move action {index + 1} later)</span>
        </Button>
        <IconButton
          label={`Remove action ${index + 1}`}
          icon={<Icons.close className="size-4" />}
          disabled={count === 1}
          onClick={onRemove}
        />
      </div>

      {entry && Object.keys(entry.config_schema).length === 0 ? (
        <p className="text-sm text-ink-500 sm:col-span-12">This action needs no settings.</p>
      ) : null}

      {entry
        ? Object.entries(entry.config_schema).map(([key, schemaType]) => {
            const spec = configFieldSpec(entry.id, key, schemaType);
            const error = rowErrors?.config?.[key]?.message ?? null;
            const name = `actions.${index}.config.${key}` as const;

            if (spec.kind === "secret") {
              const configured = Boolean(row?.secretConfigured);
              const removing = Boolean(row?.removeSecret);
              return (
                <div key={key} className="flex flex-col gap-2 sm:col-span-12">
                  <FormField
                    label={spec.label}
                    error={error}
                    hint={configured ? "A secret is saved and is never shown. Enter a new one to replace it." : spec.hint}
                  >
                    <Input
                      type="password"
                      autoComplete="new-password"
                      disabled={disabled || removing}
                      placeholder={configured ? "Saved (hidden)" : ""}
                      {...register(name)}
                    />
                  </FormField>
                  {configured ? (
                    <Checkbox label="Remove the saved secret" {...register(`actions.${index}.removeSecret`)} />
                  ) : null}
                </div>
              );
            }

            return (
              <FormField
                key={key}
                label={spec.label}
                required={spec.required}
                error={error}
                className="sm:col-span-12"
                {...(spec.hint ? { hint: spec.hint } : {})}
              >
                {spec.kind === "textarea" ? (
                  <Textarea rows={spec.json ? 4 : 2} className={spec.json ? "font-mono text-xs" : undefined} {...register(name)} />
                ) : spec.kind === "member" ? (
                  <Select {...register(name)}>
                    <option value="">Rule creator (default)</option>
                    {members.map((member) => (
                      <option key={member.id} value={member.id}>
                        {member.label}
                      </option>
                    ))}
                    {row?.config[key] && !members.some((member) => member.id === row.config[key]) ? (
                      <option value={row.config[key]}>Former or unknown member</option>
                    ) : null}
                  </Select>
                ) : spec.kind === "choice" ? (
                  <Select {...register(name)} placeholder="Choose">
                    {(spec.options ?? []).map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                    {row?.config[key] && !(spec.options ?? []).some((option) => option.value === row.config[key]) ? (
                      <option value={row.config[key]}>{row.config[key]}</option>
                    ) : null}
                  </Select>
                ) : (
                  <Input type={spec.kind === "url" ? "url" : "text"} autoComplete="off" {...register(name)} />
                )}
              </FormField>
            );
          })
        : null}
    </fieldset>
  );
}
