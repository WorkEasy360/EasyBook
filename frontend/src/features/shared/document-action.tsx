"use client";

import { appHref } from "@/lib/routes";
import * as React from "react";
import { useRouter } from "next/navigation";
import { Button, type ButtonVariant } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { FormError, FormField, Textarea } from "@/components/ui/field";
import { useToast } from "@/components/ui/toast";
import { useIdempotentMutation } from "@/lib/hooks/use-api";
import { api } from "@/lib/api/browser";
import { formErrorOf, referenceOf } from "@/lib/api/errors";

/**
 * A state transition on one record: post, void, issue, approve, cancel.
 *
 * Every one of these moves the ledger or a legal document state, so:
 *  - it always asks first, and the confirm button carries the action's own
 *    verb ("Post adjustment"), never "OK";
 *  - the request carries an Idempotency-Key minted once per submission, so a
 *    retry after a dropped connection replays instead of posting twice
 *    (spec §73);
 *  - a rejection stays IN the dialog with its reference, because a toast that
 *    fades is no place for "No fiscal year covers this posting date";
 *  - on success the Server Component page is re-rendered, so the status,
 *    totals and journal link shown come from the backend's response, not from
 *    anything assumed here.
 */

export interface DocumentActionProps {
  /** API resource, e.g. "inventory/adjustments". */
  resource: string;
  id: string;
  /** Path segment after the id, e.g. "post". */
  action: string;
  label: string;
  variant?: ButtonVariant;
  size?: "sm" | "md";
  confirmTitle: string;
  confirmMessage: React.ReactNode;
  confirmLabel?: string;
  /** Collects a reason and sends it as `{ [reason.field]: text }`. */
  reason?: { field?: string; label: string; required?: boolean; hint?: string };
  /** Extra body merged into the request. */
  body?: Record<string, unknown>;
  successTitle: string;
  /** Navigate here after success instead of refreshing in place. */
  redirectTo?: (result: unknown) => string;
  disabled?: boolean;
}

export function DocumentAction({
  resource,
  id,
  action,
  label,
  variant = "secondary",
  size = "md",
  confirmTitle,
  confirmMessage,
  confirmLabel,
  reason,
  body,
  successTitle,
  redirectTo,
  disabled = false,
}: DocumentActionProps) {
  const router = useRouter();
  const toast = useToast();
  const [open, setOpen] = React.useState(false);
  const [reasonText, setReasonText] = React.useState("");
  const [error, setError] = React.useState<{ message: string; reference: string | null } | null>(
    null,
  );

  const mutation = useIdempotentMutation<unknown, void>(
    resource,
    (_variables, idempotencyKey) => {
      const payload: Record<string, unknown> = { ...(body ?? {}) };
      if (reason) payload[reason.field ?? "reason"] = reasonText.trim();
      return api.post<unknown>(`${resource}/${id}/${action}`, payload, { idempotencyKey });
    },
    {
      onSuccess: (result) => {
        setOpen(false);
        setReasonText("");
        toast.push({ tone: "success", title: successTitle });
        if (redirectTo) router.push(appHref(redirectTo(result)));
        router.refresh();
      },
      onError: (failure) => {
        setError({
          message: formErrorOf(failure) ?? failure.message,
          reference: referenceOf(failure),
        });
      },
    },
  );

  const reasonMissing = Boolean(reason?.required) && reasonText.trim() === "";

  function close() {
    if (mutation.isPending) return;
    setOpen(false);
    setError(null);
  }

  return (
    <>
      <Button
        variant={variant}
        size={size}
        disabled={disabled}
        onClick={() => {
          setError(null);
          setOpen(true);
        }}
      >
        {label}
      </Button>

      <Dialog
        open={open}
        onClose={close}
        title={confirmTitle}
        size="sm"
        dismissible={!mutation.isPending}
        footer={
          <>
            <Button variant="secondary" onClick={close} disabled={mutation.isPending}>
              Cancel
            </Button>
            <Button
              variant={variant === "danger" ? "danger" : "primary"}
              loading={mutation.isPending}
              disabled={reasonMissing}
              onClick={() => {
                setError(null);
                mutation.mutate();
              }}
            >
              {confirmLabel ?? label}
            </Button>
          </>
        }
      >
        <div className="flex flex-col gap-3">
          <div className="text-sm text-ink-700">{confirmMessage}</div>
          {reason ? (
            <FormField
              label={reason.label}
              required={reason.required ?? false}
              {...(reason.hint ? { hint: reason.hint } : {})}
            >
              <Textarea
                rows={3}
                value={reasonText}
                onChange={(event) => setReasonText(event.target.value)}
              />
            </FormField>
          ) : null}
          <FormError message={error?.message ?? null} reference={error?.reference ?? null} />
        </div>
      </Dialog>
    </>
  );
}
