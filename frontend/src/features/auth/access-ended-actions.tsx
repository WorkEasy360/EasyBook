"use client";

import * as React from "react";
import { Button } from "@/components/ui/button";
import { SIGN_OUT_FAILED_MESSAGE, endEasyBookSession } from "@/lib/auth/sign-out";

/** An organization the person can switch to from the access-ended page. */
export interface OtherOrganization {
  id: string;
  name: string;
}

/**
 * What the "access ended" page offers (Workeasy360 entitlement,
 * backend/sso/entitlement.py): try again once an owner has renewed, or sign
 * out - or, for a person who belongs to other organizations, switch to one of
 * them. All are hard navigations, so no Server Component payload rendered for
 * the refused organization survives.
 */
export function AccessEndedActions({
  otherOrganizations = [],
  signInRequired = false,
}: {
  otherOrganizations?: OtherOrganization[];
  /** The session has no Workeasy360 account: the way forward is signing in, not trying again. */
  signInRequired?: boolean;
}) {
  const [signingOut, setSigningOut] = React.useState(false);
  const [signOutFailed, setSignOutFailed] = React.useState(false);
  const [switching, setSwitching] = React.useState<string | null>(null);
  const [switchError, setSwitchError] = React.useState(false);

  async function switchTo(organization: OtherOrganization) {
    setSwitching(organization.id);
    setSwitchError(false);
    try {
      const response = await fetch("/api/auth/organization", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ organizationId: organization.id }),
      });
      if (!response.ok) throw new Error("switch refused");
      // eslint-disable-next-line @next/next/no-location-assign-relative-destination -- a hard navigation, see above
      window.location.assign("/dashboard");
    } catch {
      setSwitchError(true);
      setSwitching(null);
    }
  }

  async function signOut() {
    setSigningOut(true);
    setSignOutFailed(false);
    // Only a sign-out that happened leads to /login; otherwise the proxy would send the
    // still-signed-in person straight back here.
    if (!(await endEasyBookSession())) {
      setSigningOut(false);
      setSignOutFailed(true);
      return;
    }
    // A HARD navigation is the point (see the component comment).
    // eslint-disable-next-line @next/next/no-location-assign-relative-destination
    window.location.assign("/login");
  }

  return (
    <div className="flex flex-col gap-2">
      {signInRequired ? (
        // A real link: the sign-in is a chain of top-level navigations (see workeasy360-button.tsx).
        <a
          href="/api/auth/workeasy360/start?next=%2Fdashboard"
          className="inline-flex h-9 w-full items-center justify-center gap-2 rounded-md border border-brand-700 bg-brand-700 px-3.5 text-sm font-medium text-white transition-colors hover:bg-brand-800"
        >
          Continue with Workeasy360
        </a>
      ) : (
        // eslint-disable-next-line @next/next/no-location-assign-relative-destination -- a hard navigation, see above
        <Button variant="primary" onClick={() => window.location.assign("/dashboard")}>
          Try again
        </Button>
      )}
      {otherOrganizations.length > 0 ? (
        <div className="mt-2 flex flex-col gap-2 border-t border-ink-200 pt-4">
          <p className="text-sm font-medium text-ink-800">Switch organization</p>
          {otherOrganizations.map((organization) => (
            <Button
              key={organization.id}
              variant="secondary"
              onClick={() => void switchTo(organization)}
              loading={switching === organization.id}
              loadingLabel="Switching"
              disabled={switching !== null}
            >
              {organization.name}
            </Button>
          ))}
          {switchError ? (
            <p role="alert" className="text-xs text-danger-600">
              Could not switch organization.
            </p>
          ) : null}
        </div>
      ) : null}
      <Button variant="secondary" onClick={() => void signOut()} loading={signingOut} loadingLabel="Signing out">
        Sign out
      </Button>
      {signOutFailed ? (
        <p role="alert" className="text-xs text-danger-600">
          {SIGN_OUT_FAILED_MESSAGE}
        </p>
      ) : null}
    </div>
  );
}
