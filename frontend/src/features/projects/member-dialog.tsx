"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { Controller, useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Checkbox, FormError, FormField } from "@/components/ui/field";
import { NumericInput } from "@/components/ui/numeric-input";
import { useToast } from "@/components/ui/toast";
import { useApiMutation } from "@/lib/hooks/use-api";
import { api } from "@/lib/api/browser";
import { fieldErrorsOf, formErrorOf, referenceOf } from "@/lib/api/errors";
import { isValidDecimal, money } from "@/lib/money";
import { MemberPicker } from "./pickers";
import type { ProjectMember, ProjectMemberInput, ProjectMemberUpdateInput } from "@/types/api/projects";

/**
 * Assign a person to a project, or change their rates.
 *
 * Two rates, deliberately separate (projects/CLAUDE.md): the BILLABLE rate is
 * what the customer is charged for this person's hours; the COST rate is what
 * the person costs the business, and drives margin. A rate change applies to
 * time logged from now on — existing entries keep the rates frozen onto them.
 *
 * The cost rate is only offered to roles that can see all timesheets: the
 * backend treats cost as the sensitive half (profitability is gated on
 * VIEW_ALL_TIMESHEETS for exactly that reason).
 */

const optionalRate = z
  .string()
  .refine((value) => value.trim() === "" || (isValidDecimal(value) && money(value).gte(0)), "A rate cannot be negative.");

const schema = z.object({
  user_id: z.string().min(1, "Choose a person."),
  billable_rate: optionalRate,
  cost_rate: optionalRate,
  is_active: z.boolean(),
});

type Values = z.infer<typeof schema>;

const blankToNull = (value: string) => (value.trim() === "" ? null : value);

function defaultsOf(member: ProjectMember | undefined): Values {
  return {
    user_id: member?.user ?? "",
    billable_rate: member?.billable_rate ?? "",
    cost_rate: member?.cost_rate ?? "",
    is_active: member?.is_active ?? true,
  };
}

export function MemberDialogButton({
  projectId,
  currency,
  member,
  memberLabel,
  assignedUserIds = [],
  canSeeCost,
}: {
  projectId: string;
  currency: string;
  member?: ProjectMember;
  /** Display name for the edit title. */
  memberLabel?: string;
  /** People already on the project, not offered again (`member_already_assigned`). */
  assignedUserIds?: readonly string[];
  canSeeCost: boolean;
}) {
  const router = useRouter();
  const toast = useToast();
  const [open, setOpen] = React.useState(false);
  const [formError, setFormError] = React.useState<{ message: string | null; reference: string | null }>({
    message: null,
    reference: null,
  });
  const formId = React.useId();

  const {
    control,
    register,
    handleSubmit,
    reset,
    setError,
    formState: { errors },
  } = useForm<Values>({ resolver: zodResolver(schema), defaultValues: defaultsOf(member) });

  const mutation = useApiMutation<ProjectMember, ProjectMemberInput | ProjectMemberUpdateInput>(
    `projects/${projectId}/members`,
    (input) =>
      member
        ? api.patch<ProjectMember>(`projects/members/${member.id}`, input)
        : api.post<ProjectMember>(`projects/${projectId}/members`, input),
    {
      onSuccess: (saved) => {
        toast.push({ tone: "success", title: member ? "Rates updated" : "Person assigned", description: saved.user_email });
        setOpen(false);
        reset(defaultsOf(member ? saved : undefined));
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
    const rates = {
      billable_rate: blankToNull(values.billable_rate),
      ...(canSeeCost ? { cost_rate: blankToNull(values.cost_rate) } : {}),
    };
    mutation.mutate(member ? { ...rates, is_active: values.is_active } : { user_id: values.user_id, ...rates });
  }

  return (
    <>
      <Button
        variant={member ? "link" : "secondary"}
        size="sm"
        onClick={() => {
          setFormError({ message: null, reference: null });
          reset(defaultsOf(member));
          setOpen(true);
        }}
      >
        {member ? (
          <>
            Edit<span className="sr-only"> rates for {memberLabel ?? member.user_email}</span>
          </>
        ) : (
          "Assign person"
        )}
      </Button>

      <Dialog
        open={open}
        onClose={() => {
          if (!mutation.isPending) setOpen(false);
        }}
        title={member ? `Rates for ${memberLabel ?? member.user_email}` : "Assign a person"}
        description="Rate changes apply to time logged from now on."
        dismissible={!mutation.isPending}
        footer={
          <>
            <Button variant="secondary" onClick={() => setOpen(false)} disabled={mutation.isPending}>
              Cancel
            </Button>
            <Button type="submit" form={formId} variant="primary" loading={mutation.isPending}>
              {member ? "Save rates" : "Assign"}
            </Button>
          </>
        }
      >
        <form id={formId} method="post" noValidate onSubmit={(event) => void handleSubmit(onSubmit)(event)} className="flex flex-col gap-4">
          <FormError message={formError.message} reference={formError.reference} />
          {member ? null : (
            <FormField label="Person" required error={errors.user_id?.message ?? null} hint="Active members of this organization.">
              <Controller
                control={control}
                name="user_id"
                render={({ field }) => (
                  <MemberPicker
                    value={field.value || null}
                    onChange={(value) => field.onChange(value ?? "")}
                    exclude={assignedUserIds}
                  />
                )}
              />
            </FormField>
          )}
          <div className="grid gap-4 sm:grid-cols-2">
            <FormField
              label="Billable rate"
              error={errors.billable_rate?.message ?? null}
              hint="Charged per hour for this person on this project. Beats the task and project rates."
            >
              <Controller
                control={control}
                name="billable_rate"
                render={({ field }) => (
                  <NumericInput nonNegative currency={currency} value={field.value} onValueChange={field.onChange} onBlur={field.onBlur} />
                )}
              />
            </FormField>
            {canSeeCost ? (
              <FormField
                label="Cost rate"
                error={errors.cost_rate?.message ?? null}
                hint="What an hour of this person costs. Unset counts as zero cost in margin."
              >
                <Controller
                  control={control}
                  name="cost_rate"
                  render={({ field }) => (
                    <NumericInput nonNegative currency={currency} value={field.value} onValueChange={field.onChange} onBlur={field.onBlur} />
                  )}
                />
              </FormField>
            ) : null}
          </div>
          {member ? (
            <Checkbox label="Active on this project" {...register("is_active")} />
          ) : null}
        </form>
      </Dialog>
    </>
  );
}
