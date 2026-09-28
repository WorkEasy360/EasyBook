"use client";

import * as React from "react";
import { Button } from "@/components/ui/button";
import { useToast } from "@/components/ui/toast";
import { useApiMutation } from "@/lib/hooks/use-api";
import { api } from "@/lib/api/browser";
import { formErrorOf, referenceOf } from "@/lib/api/errors";
import type { DownloadGrant } from "@/types/api/documents";
import { grantTarget, type GrantTarget } from "./download";

/**
 * Download: POST documents/{id}/download/ mints a short-lived signed URL
 * (default 300 s) and records an audit entry, so it happens only on click —
 * never while rendering, never prefetched, never written into the page.
 *
 * The URL is opened by NAVIGATION, not fetched: the CSP's connect-src is
 * 'self', and an S3 presigned URL is another origin. If a popup blocker eats
 * the new tab (the open happens after an await, outside the click gesture),
 * one follow-up button opens it from a fresh gesture; the grant is dropped
 * when it expires.
 */

function openTarget(target: GrantTarget, filename: string): boolean {
  if (target.kind === "same-origin") {
    const anchor = window.document.createElement("a");
    anchor.href = target.href;
    anchor.download = filename;
    anchor.rel = "noopener";
    window.document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    return true;
  }
  return window.open(target.href, "_blank", "noopener,noreferrer") !== null;
}

export function DownloadDocumentButton({
  documentId,
  filename,
  variant = "secondary",
}: {
  documentId: string;
  filename: string;
  variant?: "primary" | "secondary";
}) {
  const toast = useToast();
  const [blocked, setBlocked] = React.useState<GrantTarget | null>(null);
  const expiry = React.useRef<ReturnType<typeof setTimeout> | null>(null);

  React.useEffect(() => () => {
    if (expiry.current) clearTimeout(expiry.current);
  }, []);

  const mutation = useApiMutation<DownloadGrant, void>(
    "documents",
    () => api.post<DownloadGrant>(`documents/${documentId}/download`, {}),
    {
      onSuccess: (grant) => {
        const target = grantTarget(grant.url);
        if (!target) {
          toast.push({ tone: "error", title: "Download link not recognised", description: "Please try again." });
          return;
        }
        if (!openTarget(target, filename)) {
          setBlocked(target);
          if (expiry.current) clearTimeout(expiry.current);
          // Slightly early, so the button never offers an already-dead link.
          expiry.current = setTimeout(() => setBlocked(null), Math.max(grant.expires_in - 10, 5) * 1000);
        }
      },
      onError: (error) => {
        toast.push({
          tone: "error",
          title: "Download not available",
          description: formErrorOf(error) ?? error.message,
          ...(referenceOf(error) ? { reference: referenceOf(error) as string } : {}),
        });
      },
    },
  );

  if (blocked) {
    return (
      <Button
        variant="primary"
        onClick={() => {
          openTarget(blocked, filename);
          setBlocked(null);
        }}
      >
        Open download
      </Button>
    );
  }

  return (
    <Button variant={variant} loading={mutation.isPending} loadingLabel="Preparing download…" onClick={() => mutation.mutate()}>
      Download
    </Button>
  );
}
