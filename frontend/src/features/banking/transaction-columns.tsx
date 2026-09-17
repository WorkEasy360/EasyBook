import type { Column } from "@/components/ui/data-table";
import { Money } from "@/components/ui/money";
import { BANK_TRANSACTION_STATUS, StatusBadge } from "@/components/ui/status-badge";
import { formatDate } from "@/lib/datetime";
import { unsigned } from "./amounts";
import type { BankAccount, BankTransaction } from "@/types/api/banking";

/**
 * Statement-line columns shared by the transaction list, an account's recent
 * lines and a reconciliation's period.
 *
 * Money in and money out get their own columns, as on a bank statement. The
 * split follows the server's `is_inflow`; the out column shows the amount
 * without its minus sign, which is a string operation, not arithmetic.
 */
export function transactionColumns({
  accounts,
  showAccount,
}: {
  accounts?: Map<string, BankAccount>;
  showAccount: boolean;
}): Column<BankTransaction>[] {
  const currencyOf = (row: BankTransaction) => accounts?.get(row.bank_account)?.currency;

  return [
    {
      key: "transaction_date",
      header: "Date",
      cell: (row) => <span className="tabular whitespace-nowrap">{formatDate(row.transaction_date)}</span>,
    },
    {
      key: "description",
      header: "Description",
      cell: (row) => (
        <span>
          {row.description || <span className="italic text-ink-500">No narration</span>}
          {row.counterparty_name ? <span className="block text-xs text-ink-500">{row.counterparty_name}</span> : null}
        </span>
      ),
    },
    ...(showAccount
      ? [
          {
            key: "bank_account",
            header: "Account",
            hideBelow: "md" as const,
            cell: (row: BankTransaction) => accounts?.get(row.bank_account)?.name ?? <span className="text-ink-400">Bank account</span>,
          },
        ]
      : []),
    {
      key: "bank_reference",
      header: "Reference",
      hideBelow: "lg",
      cell: (row) => (row.bank_reference ? <span className="tabular">{row.bank_reference}</span> : <span className="text-ink-400">—</span>),
    },
    {
      key: "status",
      header: "Status",
      cell: (row) => <StatusBadge status={row.status} map={BANK_TRANSACTION_STATUS} size="sm" />,
    },
    {
      key: "money_in",
      header: "Money in",
      numeric: true,
      cell: (row) => {
        const currency = currencyOf(row);
        return row.is_inflow ? (
          <Money value={row.amount} {...(currency ? { currency } : {})} />
        ) : (
          <span className="text-ink-300">—</span>
        );
      },
    },
    {
      key: "money_out",
      header: "Money out",
      numeric: true,
      cell: (row) => {
        const currency = currencyOf(row);
        return row.is_inflow ? (
          <span className="text-ink-300">—</span>
        ) : (
          <Money value={unsigned(row.amount)} {...(currency ? { currency } : {})} />
        );
      },
    },
  ];
}
