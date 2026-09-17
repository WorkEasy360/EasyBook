"use client";

import { Button } from "./button";

/**
 * Prints the current page. The print stylesheet (globals.css) hides every
 * element marked `data-print="hide"` — navigation, actions, filters — so what
 * comes out is the document, not the application around it.
 */
export function PrintButton({ label = "Print" }: { label?: string }) {
  return (
    <Button variant="secondary" onClick={() => window.print()}>
      {label}
    </Button>
  );
}
