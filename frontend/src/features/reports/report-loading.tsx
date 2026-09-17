import { Skeleton, TableSkeleton } from "@/components/ui/states";

/**
 * Loading state for report routes (rendered by their loading.tsx). The
 * skeleton is aria-hidden; the status line is what a screen reader announces.
 */
export function ReportLoading() {
  return (
    <div>
      <div className="border-b border-ink-200 bg-white px-4 py-4 sm:px-6">
        <Skeleton className="h-3 w-24" />
        <Skeleton className="mt-3 h-6 w-56" />
      </div>
      <div className="flex flex-col gap-5 px-4 py-5 sm:px-6">
        <p role="status" className="text-sm text-ink-500">
          Loading report…
        </p>
        <div className="flex gap-2">
          <Skeleton className="h-7 w-24" />
          <Skeleton className="h-7 w-24" />
          <Skeleton className="h-7 w-32" />
        </div>
        <div className="rounded-lg border border-ink-200 bg-white">
          <TableSkeleton rows={8} columns={4} />
        </div>
      </div>
    </div>
  );
}
