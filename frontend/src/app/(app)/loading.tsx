import { Skeleton, TableSkeleton } from "@/components/ui/states";

/**
 * Shown inside the app shell while a page's server render is in flight.
 *
 * Every page here fetches from the API on the server, so without this a click
 * in the sidebar would appear to do nothing until the whole page arrived. The
 * shape roughly matches a list page (header, filters, table) so the swap to
 * real content does not jolt the layout. The status region tells a screen
 * reader something is happening; the skeleton itself is aria-hidden.
 *
 * Trade-off, accepted knowingly: streaming starts before a page can call
 * notFound(), so a missing record renders the in-shell 404 with HTTP 200
 * rather than 404. Nothing here is crawlable, and immediate feedback on every
 * navigation matters more to someone working in the app.
 */
export default function AppLoading() {
  return (
    <div aria-busy="true">
      <p role="status" className="sr-only">
        Loading…
      </p>
      <div className="border-b border-ink-200 bg-white px-4 py-4 sm:px-6">
        <Skeleton className="h-3 w-32" />
        <Skeleton className="mt-3 h-6 w-56" />
      </div>
      <div className="flex flex-col gap-5 px-4 py-5 sm:px-6">
        <div className="flex gap-2">
          <Skeleton className="h-7 w-20 rounded-full" />
          <Skeleton className="h-7 w-24 rounded-full" />
          <Skeleton className="h-7 w-16 rounded-full" />
        </div>
        <div className="rounded-lg border border-ink-200 bg-white">
          <TableSkeleton rows={8} columns={5} />
        </div>
      </div>
    </div>
  );
}
