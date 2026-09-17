import { Card, CardBody, CardHeader } from "@/components/ui/card";

/**
 * Settings the product needs but the API cannot serve yet. Listed plainly
 * instead of rendered as forms that could not save.
 *
 * Each entry is a BACKEND CONTRACT BLOCKER (verified against
 * backend/config/urls.py and every app's api/urls.py — none of these routes
 * exist).
 */
export const UNAVAILABLE_SETTINGS: Array<{ title: string; detail: string; endpoint: string; critical?: boolean }> = [
  {
    title: "Fiscal years",
    detail:
      "No fiscal year can be created or closed from the app. Posting any invoice, bill, payment or journal needs an open fiscal year covering its date, so a new organization cannot post anything until one is created directly in the database.",
    endpoint: "GET/POST fiscal years with close/reopen (accounts.FiscalYear has no API and no admin page)",
    critical: true,
  },
  {
    title: "Organization profile editing",
    detail: "Name, legal name, GSTIN, time zone and fiscal year start are shown read-only; they cannot be changed here yet.",
    endpoint: "PATCH organizations/{id}/",
  },
  {
    title: "Tax settings",
    detail: "Tax rates, GST registration details and e-invoicing settings have no API.",
    endpoint: "tax/ API (tax/api/ has no routes)",
  },
  {
    title: "Number sequences",
    detail: "Invoice, bill and other document number formats cannot be viewed or changed.",
    endpoint: "GET/PATCH number sequences (accounts.NumberSequence has no API)",
  },
  {
    title: "Default accounts",
    detail:
      "There is no organization-level default receivable, payable or tax account; forms remember the accounts used on the most recent document instead.",
    endpoint: "GET/PATCH organization default accounts",
  },
  {
    title: "Inviting members and changing roles",
    detail: "Members can be listed, but not invited, removed or given a different role.",
    endpoint: "POST organizations/members/, PATCH/DELETE organizations/members/{id}/",
  },
];

export function UnavailableSettingsCard() {
  return (
    <Card>
      <CardHeader
        title="Not available yet"
        description="These settings need backend support that does not exist yet. Nothing here can be changed from the app."
      />
      <CardBody>
        <ul className="flex flex-col divide-y divide-ink-100">
          {UNAVAILABLE_SETTINGS.map((entry) => (
            <li key={entry.title} className="flex flex-col gap-1 py-3 first:pt-0 last:pb-0">
              <span className="flex flex-wrap items-center gap-2 text-sm font-medium text-ink-900">
                {entry.title}
                {entry.critical ? (
                  <span className="rounded bg-danger-50 px-1.5 py-0.5 text-2xs font-semibold text-danger-700 ring-1 ring-danger-100">
                    Blocks posting
                  </span>
                ) : null}
              </span>
              <span className="text-sm text-ink-600">{entry.detail}</span>
              <span className="text-xs text-ink-400">Needs: {entry.endpoint}</span>
            </li>
          ))}
        </ul>
      </CardBody>
    </Card>
  );
}
