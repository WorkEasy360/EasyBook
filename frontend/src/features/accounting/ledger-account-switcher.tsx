"use client";

import { appHref } from "@/lib/routes";
import { useRouter } from "next/navigation";
import { FormField } from "@/components/ui/field";
import { AccountPicker } from "@/features/shared/pickers";

/**
 * The account choice for the general ledger. The report cannot run without
 * one (`account_required`), so picking an account navigates straight to the
 * report URL — keeping the chosen period — rather than waiting for a submit.
 * The URL stays the single source of the report's parameters.
 */
export function LedgerAccountSwitcher({
  path,
  accountId,
  preserve,
}: {
  path: string;
  accountId: string | null;
  preserve: Record<string, string | undefined>;
}) {
  const router = useRouter();

  return (
    <div className="w-full max-w-md" data-print="hide">
      <FormField label="Account" required>
        <AccountPicker
          value={accountId}
          placeholder="Choose an account…"
          onChange={(next) => {
            const params = new URLSearchParams();
            if (next) params.set("account", next);
            for (const [key, value] of Object.entries(preserve)) {
              if (value) params.set(key, value);
            }
            const query = params.toString();
            router.push(appHref(query ? `${path}?${query}` : path));
          }}
        />
      </FormField>
    </div>
  );
}
