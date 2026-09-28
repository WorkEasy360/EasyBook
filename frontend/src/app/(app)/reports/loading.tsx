import { ReportLoading } from "@/features/reports/report-loading";

/** Streams while a report page asks the engine — reports can be slow on large books. */
export default function Loading() {
  return <ReportLoading />;
}
