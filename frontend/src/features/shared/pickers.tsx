"use client";

import * as React from "react";
import { Combobox, type ComboboxOption } from "@/components/ui/combobox";
import { useLookup } from "@/lib/hooks/use-api";
import { useDebounced } from "@/lib/hooks/use-debounced";
import type { QueryParams } from "@/lib/api/query";
import type { Customer } from "@/types/api/sales";
import type { Vendor } from "@/types/api/purchases";
import type { Item } from "@/types/api/items";
import type { Account, AccountType } from "@/types/api/accounting";
import type { Warehouse } from "@/types/api/inventory";
import type { Project } from "@/types/api/projects";

/**
 * Record pickers for document forms.
 *
 * Each one is a typeahead over a paged endpoint — never a <select> holding
 * every record, because these lists are unbounded (spec §78).
 *
 * IMPORTANT: no list endpoint supports `?search=`
 * (src/lib/api/capabilities.ts), so the server cannot narrow by term. These
 * pickers therefore fetch a page and filter THAT PAGE in the browser, and say
 * so in their empty state. That is a real limitation, not a shortcut — with
 * more records than one page holds, a match further down the list cannot be
 * found by typing. Once the backend gains SearchFilter, pass the term through
 * as `search` and delete the local filtering.
 */

const PAGE_SIZE = 200;

interface PickerBaseProps {
  value: string | null;
  onChange: (value: string | null) => void;
  disabled?: boolean;
  placeholder?: string;
}

/** Narrows an already-fetched page. See the module note on why this is local. */
function localFilter<T>(
  rows: T[],
  term: string,
  fields: (row: T) => Array<string | null | undefined>,
): T[] {
  const needle = term.trim().toLowerCase();
  if (!needle) return rows;
  return rows.filter((row) =>
    fields(row).some((field) => (field ?? "").toLowerCase().includes(needle)),
  );
}

const NO_SERVER_SEARCH = "No match on this page. The API cannot search this list yet.";

export function CustomerPicker({
  value,
  onChange,
  disabled,
  placeholder,
  onSelectCustomer,
}: PickerBaseProps & { onSelectCustomer?: (customer: Customer | null) => void }) {
  const [search, setSearch] = React.useState("");
  const debounced = useDebounced(search, 200);
  const query = useLookup<Customer>("sales/customers", "customers", "", {
    extraParams: { page_size: PAGE_SIZE, is_active: "true" },
  });

  const rows = query.data?.results ?? [];
  const filtered = localFilter(rows, debounced, (row) => [
    row.display_name,
    row.customer_code,
    row.email,
  ]);

  const selected = rows.find((row) => row.id === value);

  return (
    <Combobox
      value={value}
      onChange={(next) => {
        onChange(next);
        onSelectCustomer?.(rows.find((row) => row.id === next) ?? null);
      }}
      options={filtered.map<ComboboxOption>((row) => ({
        value: row.id,
        label: row.display_name,
        hint: row.customer_code,
      }))}
      search={search}
      onSearchChange={setSearch}
      isLoading={query.isLoading}
      placeholder={placeholder ?? "Search customers…"}
      emptyMessage={rows.length === 0 ? "No customers yet" : NO_SERVER_SEARCH}
      selectedLabel={selected?.display_name ?? null}
      {...(disabled === undefined ? {} : { disabled })}
    />
  );
}

export function VendorPicker({
  value,
  onChange,
  disabled,
  placeholder,
  onSelectVendor,
}: PickerBaseProps & { onSelectVendor?: (vendor: Vendor | null) => void }) {
  const [search, setSearch] = React.useState("");
  const debounced = useDebounced(search, 200);
  const query = useLookup<Vendor>("purchases/vendors", "vendors", "", {
    extraParams: { page_size: PAGE_SIZE, is_active: "true" },
  });

  const rows = query.data?.results ?? [];
  const filtered = localFilter(rows, debounced, (row) => [
    row.display_name,
    row.vendor_code,
    row.email,
  ]);
  const selected = rows.find((row) => row.id === value);

  return (
    <Combobox
      value={value}
      onChange={(next) => {
        onChange(next);
        onSelectVendor?.(rows.find((row) => row.id === next) ?? null);
      }}
      options={filtered.map<ComboboxOption>((row) => ({
        value: row.id,
        label: row.display_name,
        hint: row.vendor_code,
      }))}
      search={search}
      onSearchChange={setSearch}
      isLoading={query.isLoading}
      placeholder={placeholder ?? "Search vendors…"}
      emptyMessage={rows.length === 0 ? "No vendors yet" : NO_SERVER_SEARCH}
      selectedLabel={selected?.display_name ?? null}
      {...(disabled === undefined ? {} : { disabled })}
    />
  );
}

export function ItemPicker({
  value,
  onChange,
  disabled,
  placeholder,
  /** Restricts to sellable or purchasable items, matching the document type. */
  usage,
  /** Only products that track inventory — for stock adjustments and transfers. */
  trackedOnly = false,
  onSelectItem,
}: PickerBaseProps & {
  usage?: "sales" | "purchase";
  trackedOnly?: boolean;
  onSelectItem?: (item: Item | null) => void;
}) {
  const [search, setSearch] = React.useState("");
  const debounced = useDebounced(search, 200);
  const query = useLookup<Item>("items", "items", "", {
    extraParams: { page_size: PAGE_SIZE, is_active: "true" },
  });

  const rows = (query.data?.results ?? []).filter((row) => {
    if (trackedOnly && !(row.item_type === "product" && row.track_inventory)) return false;
    if (usage === "sales") return row.is_sellable;
    if (usage === "purchase") return row.is_purchasable;
    return true;
  });
  const filtered = localFilter(rows, debounced, (row) => [row.name, row.sku]);
  const selected = rows.find((row) => row.id === value);

  return (
    <Combobox
      value={value}
      onChange={(next) => {
        onChange(next);
        onSelectItem?.(rows.find((row) => row.id === next) ?? null);
      }}
      options={filtered.map<ComboboxOption>((row) => ({
        value: row.id,
        label: row.name,
        hint: row.sku,
      }))}
      search={search}
      onSearchChange={setSearch}
      isLoading={query.isLoading}
      placeholder={placeholder ?? "Search items…"}
      emptyMessage={
        rows.length === 0 ? (trackedOnly ? "No items track inventory" : "No items yet") : NO_SERVER_SEARCH
      }
      selectedLabel={selected?.name ?? null}
      {...(disabled === undefined ? {} : { disabled })}
    />
  );
}

export function WarehousePicker({ value, onChange, disabled, placeholder }: PickerBaseProps) {
  const [search, setSearch] = React.useState("");
  const query = useLookup<Warehouse>("inventory/warehouses", "warehouses", "", {
    extraParams: { page_size: PAGE_SIZE, is_active: "true" },
  });

  const rows = query.data?.results ?? [];
  const filtered = localFilter(rows, search, (row) => [row.name, row.code]);
  const selected = rows.find((row) => row.id === value);

  return (
    <Combobox
      value={value}
      onChange={(next) => onChange(next)}
      options={filtered.map<ComboboxOption>((row) => ({
        value: row.id,
        label: row.name,
        hint: row.is_default ? `${row.code} · default` : row.code,
      }))}
      search={search}
      onSearchChange={setSearch}
      isLoading={query.isLoading}
      placeholder={placeholder ?? "Choose a warehouse…"}
      emptyMessage={rows.length === 0 ? "No warehouses yet" : NO_SERVER_SEARCH}
      selectedLabel={selected?.name ?? null}
      {...(disabled === undefined ? {} : { disabled })}
    />
  );
}

export function ProjectPicker({ value, onChange, disabled, placeholder }: PickerBaseProps) {
  const [search, setSearch] = React.useState("");
  const query = useLookup<Project>("projects", "projects", "", {
    extraParams: { page_size: PAGE_SIZE, status: "active" },
  });

  const rows = query.data?.results ?? [];
  const filtered = localFilter(rows, search, (row) => [row.name, row.project_code]);
  const selected = rows.find((row) => row.id === value);

  return (
    <Combobox
      value={value}
      onChange={(next) => onChange(next)}
      options={filtered.map<ComboboxOption>((row) => ({
        value: row.id,
        label: row.name,
        hint: row.project_code,
      }))}
      search={search}
      onSearchChange={setSearch}
      isLoading={query.isLoading}
      placeholder={placeholder ?? "Search projects…"}
      emptyMessage={rows.length === 0 ? "No active projects" : NO_SERVER_SEARCH}
      selectedLabel={selected?.name ?? null}
      {...(disabled === undefined ? {} : { disabled })}
    />
  );
}

/**
 * Chart-of-accounts picker.
 *
 * `accountType` narrows the list to the side of the ledger the field belongs
 * to, so a receivable field cannot be pointed at an expense account. The
 * backend validates the choice regardless.
 */
export function AccountPicker({
  value,
  onChange,
  disabled,
  placeholder,
  accountType,
}: PickerBaseProps & { accountType?: AccountType }) {
  const [search, setSearch] = React.useState("");
  const extraParams: QueryParams = { page_size: PAGE_SIZE, is_active: "true" };
  if (accountType) extraParams["account_type"] = accountType;

  const query = useLookup<Account>("accounting/accounts", `accounts:${accountType ?? "all"}`, "", {
    extraParams,
  });

  const rows = query.data?.results ?? [];
  const filtered = localFilter(rows, search, (row) => [row.name, row.code]);
  const selected = rows.find((row) => row.id === value);

  return (
    <Combobox
      value={value}
      onChange={(next) => onChange(next)}
      options={filtered.map<ComboboxOption>((row) => ({
        value: row.id,
        label: `${row.code} · ${row.name}`,
        hint: row.account_subtype || undefined,
      }))}
      search={search}
      onSearchChange={setSearch}
      isLoading={query.isLoading}
      placeholder={placeholder ?? "Choose an account…"}
      emptyMessage={rows.length === 0 ? "No matching accounts" : NO_SERVER_SEARCH}
      selectedLabel={selected ? `${selected.code} · ${selected.name}` : null}
      {...(disabled === undefined ? {} : { disabled })}
    />
  );
}
