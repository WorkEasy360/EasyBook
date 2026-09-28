"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { FormError, FormField, Input } from "@/components/ui/field";
import { useToast } from "@/components/ui/toast";
import { useApiMutation } from "@/lib/hooks/use-api";
import { api } from "@/lib/api/browser";
import { formErrorOf, referenceOf } from "@/lib/api/errors";
import type { AutomationExecution, RunRuleInput } from "@/types/api/automation";
import { isUuid } from "./contract";

/**
 * "Run now" for an ACTIVE rule — POST automation/rules/{id}/run/.
 *
 * The view creates a manual execution and ENQUEUES it for the background
 * worker (AutomationRuleRunView), so the response is a pending execution, not
 * a result. The only body key it reads is `entity_id`: the record a
 * record-based trigger's conditions are evaluated against. Triggers with no
 * record fields (manual, schedules) ignore it, so it is not asked for; for
 * the rest it is REQUIRED and must be a UUID — see contract.ts ::
 * entityLabelFor for why a blank or malformed id is never sent.
 *
 * Not idempotent on the server (no IdempotentCreateMixin) and every run can
 * notify people or call a webhook, so the confirm button is disabled while a
 * request is in flight rather than relying on a replay key.
 */
export function RunRuleDialog({
  ruleId,
  ruleName,
  entityLabel,
}: {
  ruleId: string;
  ruleName: string;
  /** e.g. "Invoice id"; null when the trigger evaluates no record. */
  entityLabel: string | null;
}) {
  const router = useRouter();
  const toast = useToast();
  const [open, setOpen] = React.useState(false);
  const [entityId, setEntityId] = React.useState("");
  const [error, setError] = React.useState<{ message: string; reference: string | null } | null>(null);
  const [entityError, setEntityError] = React.useState<string | null>(null);

  const mutation = useApiMutation<AutomationExecution, RunRuleInput>(
    "automation/rules",
    (input) => api.post<AutomationExecution>(`automation/rules/${ruleId}/run`, input),
    {
      invalidates: "automation",
      onSuccess: (execution) => {
        setOpen(false);
        setEntityId("");
        toast.push({ tone: "success", title: "Run queued", description: ruleName });
        router.push(`/automation/executions/${execution.id}`);
        router.refresh();
      },
      onError: (failure) => {
        setError({ message: formErrorOf(failure) ?? failure.message, reference: referenceOf(failure) });
      },
    },
  );

  function close() {
    if (mutation.isPending) return;
    setOpen(false);
    setError(null);
  }

  const formId = React.useId();

  return (
    <>
      <Button
        onClick={() => {
          setError(null);
          setOpen(true);
        }}
      >
        Run now
      </Button>

      <Dialog
        open={open}
        onClose={close}
        title={`Run ${ruleName} now?`}
        size="sm"
        dismissible={!mutation.isPending}
        footer={
          <>
            <Button variant="secondary" onClick={close} disabled={mutation.isPending}>
              Cancel
            </Button>
            <Button type="submit" form={formId} variant="primary" loading={mutation.isPending}>
              Run rule
            </Button>
          </>
        }
      >
        <form
          id={formId}
          method="post"
          noValidate
          className="flex flex-col gap-3"
          onSubmit={(event) => {
            event.preventDefault();
            setError(null);
            const trimmed = entityId.trim();
            if (entityLabel && !isUuid(trimmed)) {
              setEntityError("Enter the record's id — the long code at the end of its page address.");
              return;
            }
            setEntityError(null);
            mutation.mutate(entityLabel ? { entity_id: trimmed.toLowerCase() } : {});
          }}
        >
          <div className="text-sm text-ink-700">
            <p>
              The rule&apos;s conditions are checked and, if they match, its actions run — notifications are sent and
              webhooks are called. The run is queued and processed in the background.
            </p>
            <p className="mt-2">Runs count toward the rule&apos;s limit for the last 24 hours.</p>
          </div>
          {entityLabel ? (
            <FormField
              label={entityLabel}
              required
              error={entityError}
              hint="The record this run checks the rule's conditions against."
            >
              <Input value={entityId} onChange={(event) => setEntityId(event.target.value)} autoComplete="off" />
            </FormField>
          ) : null}
          <FormError message={error?.message ?? null} reference={error?.reference ?? null} />
        </form>
      </Dialog>
    </>
  );
}
