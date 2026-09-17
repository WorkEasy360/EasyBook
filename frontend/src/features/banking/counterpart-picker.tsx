"use client";

import * as React from "react";
import { Combobox, type ComboboxOption } from "@/components/ui/combobox";
import { useFormatSettings } from "@/components/providers/org-provider";
import { useLookup } from "@/lib/hooks/use-api";
import { formatMoney } from "@/lib/money";
import { formatDate } from "@/lib/datetime";
import type { BankTransfer, CreateMatchInput } from "@/types/api/banking";
import type { CustomerPayment } from "@/types/api/sales";
import type { Expense, VendorPayment } from "@/types/api/purchases";

/**
 * Candidate documents a person can match a statement line to by hand.
 *
 * The backend's own candidate search is POST …/suggestions/ (exact amount,
 * ±30 days). This picker exists for everything that search deliberately does
 * not offer — a part payment, a document outside the window — so it lists
 * documents that could LEGALLY explain the line and leaves amount and date to
 * the person.
 *
 * "Legally" is the backend's rule (services/matching.py :: _validate_counterpart):
 * a customer payment must have been received INTO this bank account's ledger
 * account, a vendor payment or expense paid FROM it (and an expense must be
 * posted), a transfer must be posted and involve this bank account. None of
 * those list endpoints can filter by account, so one page (the latest 200) is
 * fetched and narrowed here, and the empty state says so. The server
 * re-validates the choice regardless.
 */

export type CounterpartKind = CreateMatchInput["counterpart_type"];

const PAGE_SIZE = 200;
const PAGE_NOTE = "Only the latest 200 are searched, because this list cannot be filtered by bank account.";

interface Candidate {
  id: string;
  label: string;
  hint: string;
}

function useCandidates(
  kind: CounterpartKind,
  bankAccountId: string,
  ledgerAccountId: string,
  isInflow: boolean,
): { rows: Candidate[]; isLoading: boolean; error: boolean } {
  const settings = useFormatSettings();
  const money = (value: string, currency: string) =>
    formatMoney(value, { currency, locale: settings.locale, accounting: false, hideSymbol: false });

  const customerPayments = useLookup<CustomerPayment>("sales/payments", "bank-match", "", {
    enabled: kind === "customer_payment",
    extraParams: { page_size: PAGE_SIZE },
  });
  const vendorPayments = useLookup<VendorPayment>("purchases/payments", "bank-match", "", {
    enabled: kind === "vendor_payment",
    extraParams: { page_size: PAGE_SIZE },
  });
  const expenses = useLookup<Expense>("purchases/expenses", "bank-match", "", {
    enabled: kind === "expense",
    extraParams: { page_size: PAGE_SIZE, status: "posted" },
  });
  const transfers = useLookup<BankTransfer>("bank-transfers", "bank-match", "", {
    enabled: kind === "bank_transfer",
    extraParams: { page_size: PAGE_SIZE },
  });

  switch (kind) {
    case "customer_payment":
      return {
        rows: (customerPayments.data?.results ?? [])
          .filter((row) => row.destination_account === ledgerAccountId)
          .map((row) => ({
            id: row.id,
            label: row.payment_number,
            hint: `${formatDate(row.payment_date)} · ${money(row.amount, row.currency)}${row.reference ? ` · ${row.reference}` : ""}`,
          })),
        isLoading: customerPayments.isLoading,
        error: customerPayments.isError,
      };
    case "vendor_payment":
      return {
        rows: (vendorPayments.data?.results ?? [])
          .filter((row) => row.source_account === ledgerAccountId)
          .map((row) => ({
            id: row.id,
            label: row.payment_number,
            hint: `${formatDate(row.payment_date)} · ${money(row.amount, row.currency)}${row.reference ? ` · ${row.reference}` : ""}`,
          })),
        isLoading: vendorPayments.isLoading,
        error: vendorPayments.isError,
      };
    case "expense":
      return {
        rows: (expenses.data?.results ?? [])
          .filter((row) => row.status === "posted" && row.paid_through_account === ledgerAccountId)
          .map((row) => ({
            id: row.id,
            label: row.expense_number || "Expense",
            hint: `${formatDate(row.expense_date)} · ${money(row.total, row.currency)}${row.description ? ` · ${row.description}` : ""}`,
          })),
        isLoading: expenses.isLoading,
        error: expenses.isError,
      };
    case "bank_transfer":
      return {
        rows: (transfers.data?.results ?? [])
          // The side must fit the line: money in is the transfer's destination.
          .filter(
            (row) =>
              row.status === "posted" &&
              (isInflow ? row.to_bank_account === bankAccountId : row.from_bank_account === bankAccountId),
          )
          .map((row) => ({
            id: row.id,
            label: row.transfer_number,
            hint: `${formatDate(row.transfer_date)} · ${money(row.amount, row.currency)}${row.reference ? ` · ${row.reference}` : ""}`,
          })),
        isLoading: transfers.isLoading,
        error: transfers.isError,
      };
  }
}

export function CounterpartPicker({
  kind,
  bankAccountId,
  ledgerAccountId,
  isInflow,
  value,
  onChange,
}: {
  kind: CounterpartKind;
  bankAccountId: string;
  /** The bank account's ledger account — what a payment must have gone through. */
  ledgerAccountId: string;
  isInflow: boolean;
  value: string | null;
  onChange: (value: string | null) => void;
}) {
  const [search, setSearch] = React.useState("");
  const { rows, isLoading, error } = useCandidates(kind, bankAccountId, ledgerAccountId, isInflow);
  const needle = search.trim().toLowerCase();
  const filtered = needle
    ? rows.filter((row) => row.label.toLowerCase().includes(needle) || row.hint.toLowerCase().includes(needle))
    : rows;
  const selected = rows.find((row) => row.id === value);

  return (
    <Combobox
      value={value}
      onChange={(next) => onChange(next)}
      options={filtered.map<ComboboxOption>((row) => ({ value: row.id, label: row.label, hint: row.hint }))}
      search={search}
      onSearchChange={setSearch}
      isLoading={isLoading}
      placeholder="Search by number, date or amount…"
      emptyMessage={
        error
          ? "Could not load these documents, or your role cannot view them."
          : rows.length === 0
            ? `No document of this kind went through this bank account. ${PAGE_NOTE}`
            : `No match. ${PAGE_NOTE}`
      }
      selectedLabel={selected?.label ?? null}
    />
  );
}
