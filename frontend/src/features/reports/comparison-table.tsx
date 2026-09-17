import { Money } from "@/components/ui/money";
import { formatDate } from "@/lib/datetime";
import { formatPercent } from "@/lib/money";
import type { PnlTotals, ReportPeriod, Variance } from "@/types/api/reports";

const MEASURES: Array<{ key: keyof PnlTotals; label: string }> = [
  { key: "revenue", label: "Revenue" },
  { key: "cogs", label: "Cost of goods sold" },
  { key: "gross_profit", label: "Gross profit" },
  { key: "operating_expenses", label: "Operating expenses" },
  { key: "operating_profit", label: "Operating profit" },
  { key: "other_income", label: "Other income" },
  { key: "other_expense", label: "Other expenses" },
  { key: "net_profit", label: "Net profit" },
];

function periodLabel(period: ReportPeriod | { from_date: string | null; to_date: string | null }): string {
  if (period.from_date && period.to_date) return `${formatDate(period.from_date)} – ${formatDate(period.to_date)}`;
  if (period.to_date) return `Up to ${formatDate(period.to_date)}`;
  if (period.from_date) return `From ${formatDate(period.from_date)}`;
  return "All dates";
}

/**
 * The P&L comparison, totals level only — that is all the API compares (it
 * returns no per-account comparison). Current, previous, variance and
 * percentage are each the engine's `variance[measure]` values. A null
 * percentage means "from zero" and is shown as n/a, as the backend asks.
 */
export function ComparisonTable({
  variance,
  period,
  comparisonPeriod,
}: {
  variance: Record<keyof PnlTotals, Variance>;
  period: { from_date: string | null; to_date: string | null };
  comparisonPeriod: ReportPeriod;
}) {
  return (
    <div className="overflow-x-auto rounded-lg border border-ink-200 bg-white">
      <table className="w-full min-w-max border-collapse text-sm">
        <caption className="sr-only">Comparison with {periodLabel(comparisonPeriod)}</caption>
        <thead>
          <tr className="border-b border-ink-200 bg-ink-50 text-2xs font-semibold tracking-wide text-ink-600 uppercase">
            <th scope="col" className="px-4 py-2 text-left">
              Measure
            </th>
            <th scope="col" className="px-4 py-2 text-right">
              <span className="block">Current</span>
              <span className="block font-normal normal-case">{periodLabel(period)}</span>
            </th>
            <th scope="col" className="px-4 py-2 text-right">
              <span className="block">Comparison</span>
              <span className="block font-normal normal-case">{periodLabel(comparisonPeriod)}</span>
            </th>
            <th scope="col" className="px-4 py-2 text-right">
              Variance
            </th>
            <th scope="col" className="px-4 py-2 text-right">
              Change
            </th>
          </tr>
        </thead>
        <tbody className="divide-y divide-ink-100">
          {MEASURES.map((measure) => {
            const row = variance[measure.key];
            if (!row) return null;
            return (
              <tr key={measure.key}>
                <th scope="row" className="px-4 py-2 text-left font-medium text-ink-800">
                  {measure.label}
                </th>
                <td className="numeric px-4 py-2 text-right">
                  <Money value={row.current} accounting />
                </td>
                <td className="numeric px-4 py-2 text-right">
                  <Money value={row.previous} accounting />
                </td>
                <td className="numeric px-4 py-2 text-right">
                  <Money value={row.variance} accounting />
                </td>
                <td className="numeric px-4 py-2 text-right">
                  {row.variance_percent === null ? (
                    <span className="text-ink-500" title="No percentage: the comparison figure is zero">
                      n/a
                    </span>
                  ) : (
                    <span className="tabular">{formatPercent(row.variance_percent)}</span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
