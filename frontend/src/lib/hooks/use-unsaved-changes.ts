"use client";

import * as React from "react";

/**
 * Warns before losing meaningful unsaved form data (spec §74).
 *
 * Only covers a full page unload — a refresh, a close, a link to another
 * origin. Next's App Router gives no supported way to intercept a client-side
 * route change, so in-app navigation away from a dirty form is guarded by
 * confirming in the component that owns the navigation, not here. Claiming
 * otherwise would be worse than not guarding it.
 *
 * The listener is removed the moment the form is no longer dirty, so a
 * successful save never leaves a stale "are you sure" behind.
 */
export function useUnsavedChanges(isDirty: boolean): void {
  React.useEffect(() => {
    if (!isDirty) return;

    function onBeforeUnload(event: BeforeUnloadEvent) {
      // The modern contract: preventDefault, and the browser shows its own
      // fixed wording. A custom message has been ignored for years.
      event.preventDefault();
      return "";
    }

    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, [isDirty]);
}
