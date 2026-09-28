"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { Controller, useForm, useWatch } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { Button, type ButtonVariant } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { FormError, FormField, Input, Select } from "@/components/ui/field";
import { Money } from "@/components/ui/money";
import { NumericInput } from "@/components/ui/numeric-input";
import { useToast, type ToastTone } from "@/components/ui/toast";
import { AccountPicker } from "@/features/shared/pickers";
import { useIdempotentMutation } from "@/lib/hooks/use-api";
import { api } from "@/lib/api/browser";
import { fieldErrorsOf, formErrorOf, referenceOf } from "@/lib/api/errors";
import { formatDate } from "@/lib/datetime";
import { isPositiveAmount, unsigned } from "./amounts";
import { CounterpartPicker, type CounterpartKind } from "./counterpart-picker";
import {
  APPLY_RULES_REASON_LABELS,
  COUNTERPART_TYPE_LABELS,
  type ApplyRulesResult,
  type AutoMatchResult,
  type BankTransactionMatch,
  type BankTransfer,
  type CategorizeInput,
  type ConfirmTransferPairInput,
  type CreateMatchInput,
} from "@/types/api/banking";

/**
 * The matching workspace's writes. Every one is idempotent (an
 * Idempotency-Key per submission), asks first with a message that says what
 * reaches the ledger, keeps a rejection in its dialog with the request
 * reference, and re-renders the Server Component page on success so the
 * status and matches shown are the server's.
 *
 * What posts and what does not (banking/CLAUDE.md, services/matching.py):
 *  - suggest, auto-match, manual match, confirm, unmatch, exclude, restore and
 *    apply-rules post NOTHING — a matched document already posted its own
 *    journal, and a second one would double it;
 *  - categorize posts ONE journal, and uncategorize posts its reversal;
 *  - confirming a transfer pair posts ONE journal for both lines.
 * The exception inside apply-rules — a rule set to auto-confirm categorizes
 * and posts — is stated in its confirmation.
 */

type Outcome = { tone: ToastTone; title: string; description?: string };

/** Confirm → POST → a message that depends on what the server actually did. */
function ResultAction<T>({
  path,
  label,
  variant = "secondary",
  confirmTitle,
  confirmMessage,
  confirmLabel,
  describe,
}: {
  path: string;
  label: string;
  variant?: ButtonVariant;
  confirmTitle: string;
  confirmMessage: React.ReactNode;
  confirmLabel?: string;
  describe: (result: T) => Outcome;
}) {
  const router = useRouter();
  const toast = useToast();
  const [open, setOpen] = React.useState(false);
  const [error, setError] = React.useState<{ message: string; reference: string | null } | null>(null);

  const mutation = useIdempotentMutation<T, void>(
    "bank-transactions",
    (_variables, idempotencyKey) => api.post<T>(path, {}, { idempotencyKey }),
    {
      onSuccess: (result) => {
        setOpen(false);
        toast.push(describe(result));
        router.refresh();
      },
      onError: (failure) => setError({ message: formErrorOf(failure) ?? failure.message, reference: referenceOf(failure) }),
    },
  );

  function close() {
    if (mutation.isPending) return;
    setOpen(false);
    setError(null);
  }

  return (
    <>
      <Button
        variant={variant}
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
            <Button variant="primary" loading={mutation.isPending} onClick={() => mutation.mutate()}>
              {confirmLabel ?? label}
            </Button>
          </>
        }
      >
        <div className="flex flex-col gap-3">
          <div className="text-sm text-ink-700">{confirmMessage}</div>
          <FormError message={error?.message ?? null} reference={error?.reference ?? null} />
        </div>
      </Dialog>
    </>
  );
}

export function FindSuggestionsButton({ transactionId }: { transactionId: string }) {
  return (
    <ResultAction<BankTransactionMatch[]>
      path={`bank-transactions/${transactionId}/suggestions`}
      label="Find suggestions"
      confirmTitle="Look for matching documents?"
      confirmMessage={
        <>
          <p>
            Searches customer payments, vendor payments, expenses, transfers and journals on this bank account for the
            exact amount within 30 days, and lists up to five as suggestions.
          </p>
          <p className="mt-2">It replaces any earlier unconfirmed suggestions, confirms nothing and posts nothing.</p>
        </>
      }
      confirmLabel="Find suggestions"
      describe={(matches) =>
        matches.length > 0
          ? { tone: "success", title: `${matches.length} ${matches.length === 1 ? "suggestion" : "suggestions"} found`, description: "Review and confirm the right one." }
          : { tone: "info", title: "No suggestions", description: "No document on this account has this exact amount within 30 days." }
      }
    />
  );
}

export function AutoMatchButton({ transactionId }: { transactionId: string }) {
  return (
    <ResultAction<AutoMatchResult>
      path={`bank-transactions/${transactionId}/auto-match`}
      label="Auto-match"
      confirmTitle="Auto-match this transaction?"
      confirmMessage={
        <>
          <p>
            Confirms a match only when exactly ONE document scores strongly enough (the exact amount on the same day, or
            better). If two documents are equally likely, nothing is confirmed and the choice stays with you.
          </p>
          <p className="mt-2">A match posts nothing: the document already posted its own journal.</p>
        </>
      }
      describe={(result) =>
        result.matched && result.match
          ? { tone: "success", title: "Matched", description: result.match.reason || "Confirmed against a single document." }
          : { tone: "info", title: "Nothing confirmed", description: "There was no single strong candidate. Find suggestions or match it by hand." }
      }
    />
  );
}

export function ApplyRulesButton({ transactionId }: { transactionId: string }) {
  return (
    <ResultAction<ApplyRulesResult>
      path={`bank-transactions/${transactionId}/apply-rules`}
      label="Apply rules"
      confirmTitle="Run the bank rules on this transaction?"
      confirmMessage={
        <>
          <p>Active rules are tried in priority order and the first one that matches decides.</p>
          <p className="mt-2">
            An exclude rule excludes the line. A categorize rule posts a journal <strong>only</strong> if it is set to
            auto-confirm; otherwise nothing changes and you are told which rule matched.
          </p>
        </>
      }
      describe={(result) => ({
        tone: result.applied ? "success" : "info",
        title: result.rule ? `Rule: ${result.rule.name}` : "No rule applied",
        description: APPLY_RULES_REASON_LABELS[result.reason] ?? result.reason,
      })}
    />
  );
}

/** DELETE bank-matches/{id}/ — DocumentAction only POSTs, so this is its DELETE twin. */
export function RemoveMatchButton({
  matchId,
  label,
  confirmTitle,
  confirmMessage,
}: {
  matchId: string;
  label: string;
  confirmTitle: string;
  confirmMessage: React.ReactNode;
}) {
  const router = useRouter();
  const toast = useToast();
  const [open, setOpen] = React.useState(false);
  const [error, setError] = React.useState<{ message: string; reference: string | null } | null>(null);

  const mutation = useIdempotentMutation<unknown, void>(
    "bank-transactions",
    (_variables, idempotencyKey) => api.delete<unknown>(`bank-matches/${matchId}`, { idempotencyKey }),
    {
      onSuccess: () => {
        setOpen(false);
        toast.push({ tone: "success", title: label === "Dismiss" ? "Suggestion dismissed" : "Match removed" });
        router.refresh();
      },
      onError: (failure) => setError({ message: formErrorOf(failure) ?? failure.message, reference: referenceOf(failure) }),
    },
  );

  function close() {
    if (mutation.isPending) return;
    setOpen(false);
    setError(null);
  }

  return (
    <>
      <Button size="sm" variant="secondary" onClick={() => setOpen(true)}>
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
            <Button variant="danger" loading={mutation.isPending} onClick={() => mutation.mutate()}>
              {label}
            </Button>
          </>
        }
      >
        <div className="flex flex-col gap-3">
          <div className="text-sm text-ink-700">{confirmMessage}</div>
          <FormError message={error?.message ?? null} reference={error?.reference ?? null} />
        </div>
      </Dialog>
    </>
  );
}

const categorizeSchema = z.object({
  account_id: z.string().min(1, "Choose the account this transaction belongs to."),
  description: z.string().max(255),
});

type CategorizeValues = z.infer<typeof categorizeSchema>;

/**
 * POST bank-transactions/{id}/categorize/ — the one matching action that posts.
 *
 * For a line with no document behind it (a bank charge, interest, an owner's
 * contribution). The server posts the journal for the part of the line not
 * already explained by confirmed matches: money in debits the bank's ledger
 * account and credits the chosen account; money out the reverse.
 */
export function CategorizeButton({
  transactionId,
  amount,
  currency,
  isInflow,
  transactionDate,
  bankLedgerLabel,
  hasConfirmedMatches,
}: {
  transactionId: string;
  /** The line's signed amount, as the API returned it. */
  amount: string;
  currency: string;
  isInflow: boolean;
  transactionDate: string;
  bankLedgerLabel: string;
  hasConfirmedMatches: boolean;
}) {
  const router = useRouter();
  const toast = useToast();
  const [open, setOpen] = React.useState(false);
  const [formError, setFormError] = React.useState<{ message: string | null; reference: string | null }>({
    message: null,
    reference: null,
  });

  const {
    control,
    register,
    handleSubmit,
    reset,
    setError,
    formState: { errors },
  } = useForm<CategorizeValues>({
    resolver: zodResolver(categorizeSchema),
    defaultValues: { account_id: "", description: "" },
  });

  const mutation = useIdempotentMutation<BankTransactionMatch, CategorizeInput>(
    "bank-transactions",
    (input, idempotencyKey) =>
      api.post<BankTransactionMatch>(`bank-transactions/${transactionId}/categorize`, input, { idempotencyKey }),
    {
      invalidates: "bank-reconciliations",
      onSuccess: (match) => {
        setOpen(false);
        reset();
        toast.push({ tone: "success", title: "Transaction categorized", description: match.reason || "Its journal is linked on this page." });
        router.refresh();
      },
      onError: (error) => {
        setFormError({ message: formErrorOf(error), reference: referenceOf(error) });
        for (const [field, messages] of Object.entries(fieldErrorsOf(error))) {
          if (field in categorizeSchema.shape && messages[0]) {
            setError(field as keyof CategorizeValues, { type: "server", message: messages[0] });
          }
        }
      },
    },
  );

  const formId = React.useId();

  return (
    <>
      <Button variant="primary" onClick={() => setOpen(true)}>
        Categorize
      </Button>
      <Dialog
        open={open}
        onClose={() => {
          if (!mutation.isPending) setOpen(false);
        }}
        title="Categorize and post a journal"
        size="md"
        dismissible={!mutation.isPending}
        footer={
          <>
            <Button variant="secondary" onClick={() => setOpen(false)} disabled={mutation.isPending}>
              Cancel
            </Button>
            <Button type="submit" form={formId} variant="primary" loading={mutation.isPending}>
              Categorize and post
            </Button>
          </>
        }
      >
        <form
          id={formId}
          method="post"
          noValidate
          onSubmit={(event) => {
            setFormError({ message: null, reference: null });
            void handleSubmit((values) => mutation.mutate(values))(event);
          }}
          className="flex flex-col gap-4"
        >
          <div className="text-sm text-ink-700">
            <p>
              Use this when no document exists for the line — a bank charge, interest, an owner&apos;s contribution. If a
              payment or expense was already recorded, match to it instead, or the amount is booked twice.
            </p>
            <p className="mt-2">
              Posts a journal dated {formatDate(transactionDate)} for{" "}
              {hasConfirmedMatches ? (
                "the part of this line not already explained by its confirmed matches"
              ) : (
                <Money value={unsigned(amount)} currency={currency} />
              )}
              : {isInflow ? `debit ${bankLedgerLabel}, credit the account below` : `debit the account below, credit ${bankLedgerLabel}`}
              . Undo it with Uncategorize, which posts a reversal.
            </p>
          </div>
          <FormError message={formError.message} reference={formError.reference} />
          <FormField
            label="Account"
            required
            error={errors.account_id?.message ?? null}
            hint="Any account except this bank account's own ledger account."
          >
            <Controller
              control={control}
              name="account_id"
              render={({ field }) => (
                <AccountPicker value={field.value || null} onChange={(value) => field.onChange(value ?? "")} />
              )}
            />
          </FormField>
          <FormField label="Journal memo" error={errors.description?.message ?? null} hint="Optional. Shown on the journal.">
            <Input {...register("description")} />
          </FormField>
        </form>
      </Dialog>
    </>
  );
}

const matchSchema = z.object({
  counterpart_type: z.enum(["customer_payment", "vendor_payment", "expense", "bank_transfer"]),
  counterpart_id: z.string().min(1, "Choose the document."),
  amount: z.string().refine((value) => value === "" || isPositiveAmount(value), "Enter an amount above zero, or leave blank."),
});

type MatchValues = z.infer<typeof matchSchema>;

/**
 * POST bank-transactions/{id}/matches/ — a person says "this line is that
 * document". Created CONFIRMED (CreateMatchSerializer passes confirm=True).
 * Only the document kinds that can explain money moving this direction are
 * offered (services/matching.py: match_direction_mismatch).
 */
export function CreateMatchButton({
  transactionId,
  bankAccountId,
  ledgerAccountId,
  isInflow,
  amount,
  currency,
}: {
  transactionId: string;
  bankAccountId: string;
  ledgerAccountId: string;
  isInflow: boolean;
  amount: string;
  currency: string;
}) {
  const router = useRouter();
  const toast = useToast();
  const [open, setOpen] = React.useState(false);
  const [formError, setFormError] = React.useState<{ message: string | null; reference: string | null }>({
    message: null,
    reference: null,
  });

  const kinds: CounterpartKind[] = isInflow
    ? ["customer_payment", "bank_transfer"]
    : ["vendor_payment", "expense", "bank_transfer"];

  const {
    control,
    register,
    handleSubmit,
    reset,
    setValue,
    formState: { errors },
  } = useForm<MatchValues>({
    resolver: zodResolver(matchSchema),
    defaultValues: { counterpart_type: kinds[0], counterpart_id: "", amount: "" },
  });
  const kind = useWatch({ control, name: "counterpart_type" });

  const mutation = useIdempotentMutation<BankTransactionMatch, CreateMatchInput>(
    "bank-transactions",
    (input, idempotencyKey) =>
      api.post<BankTransactionMatch>(`bank-transactions/${transactionId}/matches`, input, { idempotencyKey }),
    {
      onSuccess: (match) => {
        setOpen(false);
        reset();
        toast.push({ tone: "success", title: "Match confirmed", description: COUNTERPART_TYPE_LABELS[match.counterpart_type] });
        router.refresh();
      },
      onError: (error) => setFormError({ message: formErrorOf(error), reference: referenceOf(error) }),
    },
  );

  const formId = React.useId();

  return (
    <>
      <Button onClick={() => setOpen(true)}>Match to a document</Button>
      <Dialog
        open={open}
        onClose={() => {
          if (!mutation.isPending) setOpen(false);
        }}
        title="Match to a recorded document"
        size="md"
        dismissible={!mutation.isPending}
        footer={
          <>
            <Button variant="secondary" onClick={() => setOpen(false)} disabled={mutation.isPending}>
              Cancel
            </Button>
            <Button type="submit" form={formId} variant="primary" loading={mutation.isPending}>
              Confirm match
            </Button>
          </>
        }
      >
        <form
          id={formId}
          method="post"
          noValidate
          onSubmit={(event) => {
            setFormError({ message: null, reference: null });
            void handleSubmit((values) =>
              mutation.mutate({
                counterpart_type: values.counterpart_type,
                counterpart_id: values.counterpart_id,
                amount: values.amount === "" ? null : values.amount,
              }),
            )(event);
          }}
          className="flex flex-col gap-4"
        >
          <p className="text-sm text-ink-700">
            Records that this statement line is money already booked by the document you choose. It posts nothing — the
            document posted its own journal. The server refuses a document on another bank account, in the wrong direction,
            or one already fully matched elsewhere.
          </p>
          <FormError message={formError.message} reference={formError.reference} />
          <FormField label="Document type" required>
            <Select
              {...register("counterpart_type", {
                onChange: () => setValue("counterpart_id", ""),
              })}
            >
              {kinds.map((value) => (
                <option key={value} value={value}>
                  {COUNTERPART_TYPE_LABELS[value]}
                </option>
              ))}
            </Select>
          </FormField>
          <FormField label="Document" required error={errors.counterpart_id?.message ?? null}>
            <Controller
              control={control}
              name="counterpart_id"
              render={({ field }) => (
                <CounterpartPicker
                  key={kind}
                  kind={kind}
                  bankAccountId={bankAccountId}
                  ledgerAccountId={ledgerAccountId}
                  isInflow={isInflow}
                  value={field.value || null}
                  onChange={(value) => field.onChange(value ?? "")}
                />
              )}
            />
          </FormField>
          <FormField
            label="Amount explained"
            error={errors.amount?.message ?? null}
            hint={
              <>
                Leave blank for the whole line (<Money value={unsigned(amount)} currency={currency} />). Enter less when this
                document covers only part of it; the line stays open until its matches cover the full amount.
              </>
            }
          >
            <Controller
              control={control}
              name="amount"
              render={({ field }) => (
                <NumericInput currency={currency} nonNegative value={field.value} onValueChange={field.onChange} onBlur={field.onBlur} />
              )}
            />
          </FormField>
        </form>
      </Dialog>
    </>
  );
}

/**
 * POST bank-transfers/confirm-pair/ — two statement lines on two of our
 * accounts are one movement. Posts exactly ONE journal for the pair and
 * matches both lines to the new transfer; booking each line separately would
 * double the movement.
 */
export function ConfirmTransferPairButton({
  outflowTransactionId,
  inflowTransactionId,
  amount,
  currency,
  fromName,
  toName,
  transferDate,
}: {
  outflowTransactionId: string;
  inflowTransactionId: string;
  /** Unsigned amount of the pair. */
  amount: string;
  currency: string;
  fromName: string;
  toName: string;
  /** The OUTFLOW line's date — the day the money left. */
  transferDate: string;
}) {
  const router = useRouter();
  const toast = useToast();
  const [open, setOpen] = React.useState(false);
  const [reference, setReference] = React.useState("");
  const [error, setError] = React.useState<{ message: string; reference: string | null } | null>(null);

  const mutation = useIdempotentMutation<BankTransfer, ConfirmTransferPairInput>(
    "bank-transfers",
    (input, idempotencyKey) => api.post<BankTransfer>("bank-transfers/confirm-pair", input, { idempotencyKey }),
    {
      onSuccess: (transfer) => {
        setOpen(false);
        toast.push({ tone: "success", title: `Transfer ${transfer.transfer_number} recorded`, description: "Both lines are now matched." });
        router.refresh();
      },
      onError: (failure) => setError({ message: formErrorOf(failure) ?? failure.message, reference: referenceOf(failure) }),
    },
  );

  function close() {
    if (mutation.isPending) return;
    setOpen(false);
    setError(null);
  }

  return (
    <>
      <Button size="sm" variant="primary" onClick={() => setOpen(true)}>
        Confirm transfer
      </Button>
      <Dialog
        open={open}
        onClose={close}
        title="Record these two lines as one transfer?"
        size="sm"
        dismissible={!mutation.isPending}
        footer={
          <>
            <Button variant="secondary" onClick={close} disabled={mutation.isPending}>
              Cancel
            </Button>
            <Button
              variant="primary"
              loading={mutation.isPending}
              onClick={() =>
                mutation.mutate({
                  outflow_transaction_id: outflowTransactionId,
                  inflow_transaction_id: inflowTransactionId,
                  reference,
                })
              }
            >
              Record transfer
            </Button>
          </>
        }
      >
        <div className="flex flex-col gap-3">
          <div className="text-sm text-ink-700">
            <p>
              Records one transfer of <Money value={amount} currency={currency} /> from {fromName} to {toName}, dated{" "}
              {formatDate(transferDate)}, and posts one journal: debit {toName}&apos;s ledger account, credit {fromName}
              &apos;s. Both statement lines are matched to it.
            </p>
            <p className="mt-2">Nothing here is income or expense. To undo, unmatch both lines and void the transfer.</p>
          </div>
          <FormField label="Reference" hint="Optional.">
            <Input value={reference} onChange={(event) => setReference(event.target.value)} />
          </FormField>
          <FormError message={error?.message ?? null} reference={error?.reference ?? null} />
        </div>
      </Dialog>
    </>
  );
}
