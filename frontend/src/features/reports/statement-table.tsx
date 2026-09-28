import { appHref } from "@/lib/routes";
import Link from "next/link";
import { Money } from "@/components/ui/money";
import { cn } from "@/lib/cn";
import type { StatementLine } from "./statement";

/**
 * A financial statement as a two-column table: line and amount.
 *
 * Amounts use accounting presentation (negatives in parentheses, spoken as
 * "negative" by <Money>). Every figure is one the API returned — the lines are
 * built by statement.ts, which does no arithmetic.
 */
export function StatementTable({
  caption,
  lines,
  accountHref,
}: {
  caption: string;
  lines: StatementLine[];
  /** Drill-down to the account's ledger for the same period. */
  accountHref?: (accountId: string) => string;
}) {
  return (
    <div className="overflow-x-auto rounded-lg border border-ink-200 bg-white">
      <table className="w-full border-collapse text-sm">
        <caption className="sr-only">{caption}</caption>
        <thead>
          <tr className="border-b border-ink-200 bg-ink-50">
            <th scope="col" className="px-4 py-2 text-left text-2xs font-semibold tracking-wide text-ink-600 uppercase">
              Account
            </th>
            <th scope="col" className="px-4 py-2 text-right text-2xs font-semibold tracking-wide text-ink-600 uppercase">
              Amount
            </th>
          </tr>
        </thead>
        <tbody>
          {lines.map((line) => {
            switch (line.kind) {
              case "heading":
                return (
                  <tr key={line.key} className={line.level === 1 ? "border-t border-ink-200" : undefined}>
                    <th
                      scope="colgroup"
                      colSpan={2}
                      className={cn(
                        "px-4 text-left",
                        line.level === 1
                          ? "pt-4 pb-1.5 text-sm font-semibold text-ink-900"
                          : "pt-2 pb-1 pl-6 text-2xs font-medium tracking-wide text-ink-500 uppercase",
                      )}
                    >
                      {line.label}
                    </th>
                  </tr>
                );
              case "empty":
                return (
                  <tr key={line.key}>
                    <td colSpan={2} className="px-4 py-1.5 pl-8 text-sm text-ink-400 italic">
                      {line.label}
                    </td>
                  </tr>
                );
              case "row":
                return (
                  <tr key={line.key} className="hover:bg-ink-50/60">
                    <td className="px-4 py-1.5 pl-8">
                      {line.code ? <span className="tabular mr-2 text-ink-500">{line.code}</span> : null}
                      {line.accountId && accountHref ? (
                        <Link href={appHref(accountHref(line.accountId))} className="text-brand-700 hover:underline">
                          {line.label}
                        </Link>
                      ) : (
                        <span className="text-ink-800">{line.label}</span>
                      )}
                    </td>
                    <td className="numeric px-4 py-1.5 text-right">
                      <Money value={line.amount} accounting />
                    </td>
                  </tr>
                );
              case "total":
                return (
                  <tr
                    key={line.key}
                    className={cn(
                      line.weight === "section" && "border-t border-ink-200",
                      line.weight === "subtotal" && "border-t border-ink-300 bg-ink-50/70",
                      line.weight === "grand" && "border-t-2 border-ink-400 bg-ink-50",
                    )}
                  >
                    <th
                      scope="row"
                      className={cn(
                        "px-4 py-2 text-left",
                        line.weight === "section" ? "pl-6 font-medium text-ink-800" : "font-semibold text-ink-900",
                        line.weight === "grand" && "text-base",
                      )}
                    >
                      {line.label}
                    </th>
                    <td className={cn("numeric px-4 py-2 text-right", line.weight === "grand" && "text-base")}>
                      <Money value={line.amount} accounting strong={line.weight !== "section"} />
                    </td>
                  </tr>
                );
              default:
                return null;
            }
          })}
        </tbody>
      </table>
    </div>
  );
}
