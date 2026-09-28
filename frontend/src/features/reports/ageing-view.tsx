import { DataTable, TableFooterRow, type Column } from "@/components/ui/data-table";
import { Section } from "@/components/ui/detail";
import { Money } from "@/components/ui/money";
import { StatCard, StatGrid } from "@/components/ui/stat-card";
import { EmptyState } from "@/components/ui/states";
import { wholeList } from "@/lib/list-query";
import { AGEING_BUCKETS, AGEING_BUCKET_LABELS, type AgeingBucket, type AgeingReport } from "@/types/api/reports";

/** "90+" is not a valid fragment id character set on its own; keep anchors plain. */
export function bucketAnchor(bucket: AgeingBucket): string {
  return `bucket-${bucket.replace("+", "-plus")}`;
}

/**
 * Receivables or payables ageing: the engine's bucket totals up top, then the
 * documents in each non-empty bucket. Every total shown — per bucket and
 * overall — is the API's `totals[bucket]` / `grand_total`, never a sum of the
 * rows on screen.
 */
export function AgeingView<Row>({
  report,
  columns,
  getRowId,
  getRowHref,
  documentNoun,
  emptyDescription,
  pathname,
}: {
  report: AgeingReport<Row>;
  columns: Column<Row>[];
  getRowId: (row: Row) => string;
  getRowHref: (row: Row) => string;
  documentNoun: string;
  emptyDescription: string;
  pathname: string;
}) {
  const nonEmpty = AGEING_BUCKETS.filter((bucket) => (report.buckets[bucket] ?? []).length > 0);

  return (
    <>
      <StatGrid columns={6}>
        {AGEING_BUCKETS.map((bucket) => (
          <StatCard
            key={bucket}
            label={AGEING_BUCKET_LABELS[bucket]}
            value={<Money value={report.totals[bucket]} colorNegative={false} />}
            hint={`${(report.buckets[bucket] ?? []).length} ${documentNoun}${(report.buckets[bucket] ?? []).length === 1 ? "" : "s"}`}
            {...((report.buckets[bucket] ?? []).length > 0 ? { href: `#${bucketAnchor(bucket)}` } : {})}
          />
        ))}
        <StatCard label="Total outstanding" value={<Money value={report.grand_total} strong colorNegative={false} />} />
      </StatGrid>

      {nonEmpty.length === 0 ? (
        <div className="rounded-lg border border-ink-200 bg-white">
          <EmptyState title={`No outstanding ${documentNoun}s`} description={emptyDescription} />
        </div>
      ) : (
        nonEmpty.map((bucket) => (
          <Section key={bucket} title={AGEING_BUCKET_LABELS[bucket]} className="scroll-mt-4">
            <div id={bucketAnchor(bucket)}>
              <DataTable
                caption={`${AGEING_BUCKET_LABELS[bucket]} ${documentNoun}s`}
                columns={columns}
                data={wholeList(report.buckets[bucket])}
                getRowId={getRowId}
                getRowHref={getRowHref}
                page={1}
                pageSize={Math.max(report.buckets[bucket].length, 1)}
                buildPageHref={() => pathname}
                footer={
                  <TableFooterRow
                    label={`${AGEING_BUCKET_LABELS[bucket]} total`}
                    columnCount={columns.length}
                    values={[{ key: "total", node: <Money value={report.totals[bucket]} strong /> }]}
                  />
                }
              />
            </div>
          </Section>
        ))
      )}
    </>
  );
}
