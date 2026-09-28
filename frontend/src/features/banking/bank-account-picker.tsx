"use client";

import * as React from "react";
import { Combobox, type ComboboxOption } from "@/components/ui/combobox";
import { BANK_ACCOUNT_KIND_LABELS } from "@/components/ui/status-badge";
import { useLookup } from "@/lib/hooks/use-api";
import type { BankAccount } from "@/types/api/banking";

/**
 * Bank account picker for transfers, rules and reconciliations.
 *
 * Same shape as features/shared/pickers.tsx: bank-accounts has no `?search=`
 * (capabilities.ts), so one page is fetched and narrowed locally. An
 * organization with more than 200 bank accounts is not a realistic case.
 */

const PAGE_SIZE = 200;

export function BankAccountPicker({
  value,
  onChange,
  disabled,
  placeholder,
  activeOnly = true,
  excludeId,
  onSelectAccount,
}: {
  value: string | null;
  onChange: (value: string | null) => void;
  disabled?: boolean;
  placeholder?: string;
  /** Transfers and imports refuse inactive accounts (bank_account_inactive). */
  activeOnly?: boolean;
  /** Hides one account — the other side of a transfer. */
  excludeId?: string | null;
  onSelectAccount?: (account: BankAccount | null) => void;
}) {
  const [search, setSearch] = React.useState("");
  const query = useLookup<BankAccount>("bank-accounts", `bank-accounts:${activeOnly ? "active" : "all"}`, "", {
    extraParams: { page_size: PAGE_SIZE, ...(activeOnly ? { is_active: "true" } : {}) },
  });

  const rows = (query.data?.results ?? []).filter((row) => row.id !== excludeId);
  const needle = search.trim().toLowerCase();
  const filtered = needle
    ? rows.filter((row) =>
        [row.name, row.bank_name, row.masked_number].some((field) => field.toLowerCase().includes(needle)),
      )
    : rows;
  const selected = query.data?.results.find((row) => row.id === value);

  return (
    <Combobox
      value={value}
      onChange={(next) => {
        onChange(next);
        onSelectAccount?.(query.data?.results.find((row) => row.id === next) ?? null);
      }}
      options={filtered.map<ComboboxOption>((row) => ({
        value: row.id,
        label: row.name,
        hint: [BANK_ACCOUNT_KIND_LABELS[row.kind] ?? row.kind, row.bank_name, row.masked_number, row.currency]
          .filter(Boolean)
          .join(" · "),
      }))}
      search={search}
      onSearchChange={setSearch}
      isLoading={query.isLoading}
      placeholder={placeholder ?? "Choose a bank account…"}
      emptyMessage={rows.length === 0 ? "No bank accounts yet" : "No match on this page."}
      selectedLabel={selected?.name ?? null}
      {...(disabled === undefined ? {} : { disabled })}
    />
  );
}
