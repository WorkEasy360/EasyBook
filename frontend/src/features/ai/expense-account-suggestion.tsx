"use client";

import * as React from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { FormError } from "@/components/ui/field";
import { useIdempotentMutation } from "@/lib/hooks/use-api";
import { api } from "@/lib/api/browser";
import { formErrorOf, referenceOf } from "@/lib/api/errors";
import type { ExpenseAccountSuggestionResponse } from "@/types/api/ai";
import { AI_DISABLED_MESSAGE, isAiDisabledError } from "./sources";

/**
 * "Suggest an account" for an expense description:
 * POST ai/suggestions/expense-account/.
 *
 * The backend chooses only from this organization's ACTIVE EXPENSE accounts
 * and discards anything else the model says (`suggested_account: null`), and
 * it applies nothing (`applied: false`). This component likewise changes
 * nothing on its own: the suggestion is shown with its confidence and
 * reasoning, and only the user's click on "Use this account" hands it to the
 * host form through `onPick`. The form still saves through the normal expense
 * workflow, where the backend validates the account again.
 *
 * Requires PERMISSIONS.USE_AI_ASSISTANT and PERMISSIONS.VIEW_ACCOUNTING — the
 * backend refuses the call without the latter. Mount beside the expense
 * account picker in the expense form, passing the current description (and
 * vendor name when known).
 */

const CONFIDENCE_TONE = { low: "neutral", medium: "info", high: "success" } as const;

export function ExpenseAccountSuggestion({
  description,
  vendorName,
  onPick,
  disabled: disabledByHost = false,
}: {
  description: string;
  vendorName?: string;
  onPick: (account: { id: string; code: string; name: string }) => void;
  disabled?: boolean;
}) {
  const [suggestion, setSuggestion] = React.useState<ExpenseAccountSuggestionResponse | null>(null);
  const [aiDisabled, setAiDisabled] = React.useState(false);
  const [error, setError] = React.useState<{ message: string; reference: string | null } | null>(null);

  const mutation = useIdempotentMutation<ExpenseAccountSuggestionResponse, { description: string; vendor_name?: string }>(
    "ai/suggestions",
    (input, idempotencyKey) =>
      api.post<ExpenseAccountSuggestionResponse>("ai/suggestions/expense-account", input, {
        idempotencyKey,
        timeoutMs: 60_000,
      }),
    {
      onSuccess: (result) => setSuggestion(result),
      onError: (failure) => {
        if (isAiDisabledError(failure)) {
          setAiDisabled(true);
          return;
        }
        setError({ message: formErrorOf(failure) ?? failure.message, reference: referenceOf(failure) });
      },
    },
  );

  const text = description.trim();

  return (
    <div className="flex flex-col gap-2" aria-live="polite">
      <div>
        <Button
          size="sm"
          variant="secondary"
          loading={mutation.isPending}
          loadingLabel="Finding a suggestion…"
          disabled={disabledByHost || aiDisabled || text.length === 0}
          onClick={() => {
            setError(null);
            setSuggestion(null);
            const vendor = vendorName?.trim();
            mutation.mutate(vendor ? { description: text, vendor_name: vendor } : { description: text });
          }}
        >
          Suggest an account
        </Button>
      </div>

      {aiDisabled ? <p className="text-xs text-ink-600">{AI_DISABLED_MESSAGE}</p> : null}
      <FormError message={error?.message ?? null} reference={error?.reference ?? null} />

      {suggestion ? (
        <div className="rounded-md border border-ink-200 bg-ink-50 px-3 py-2 text-sm">
          {suggestion.suggested_account ? (
            <>
              <p className="flex flex-wrap items-center gap-2">
                <span className="text-ink-600">Suggested:</span>
                <span className="font-medium text-ink-900">
                  {suggestion.suggested_account.code} · {suggestion.suggested_account.name}
                </span>
                <Badge tone={CONFIDENCE_TONE[suggestion.confidence] ?? "neutral"} size="sm">
                  {suggestion.confidence} confidence
                </Badge>
              </p>
              {suggestion.reasoning ? <p className="mt-1 text-xs text-ink-600">{suggestion.reasoning}</p> : null}
              <div className="mt-2 flex items-center gap-3">
                <Button
                  size="sm"
                  variant="primary"
                  onClick={() => {
                    if (suggestion.suggested_account) onPick(suggestion.suggested_account);
                  }}
                >
                  Use this account
                </Button>
                <span className="text-xs text-ink-500">Nothing changes until you choose it and save.</span>
              </div>
            </>
          ) : (
            <p className="text-ink-700">
              No account could be suggested with confidence.
              {suggestion.reasoning ? <span className="block text-xs text-ink-500">{suggestion.reasoning}</span> : null}
            </p>
          )}
        </div>
      ) : null}
    </div>
  );
}
