import type { PartyAddress } from "@/types/api/purchases";

/** One labelled postal address on a vendor page. Server-renderable. */
export function AddressBlock({ label, address }: { label: string; address: PartyAddress | null | undefined }) {
  const lines = address
    ? [
        address.line1,
        address.line2,
        [address.city, address.state].filter(Boolean).join(", "),
        address.postal_code,
        address.country,
      ].filter((line): line is string => Boolean(line && line.trim()))
    : [];

  return (
    <div>
      <p className="text-2xs font-medium tracking-wide text-ink-500 uppercase">{label}</p>
      {lines.length > 0 ? (
        <address className="mt-1 text-sm not-italic text-ink-800">
          {lines.map((line) => (
            <span key={line} className="block">
              {line}
            </span>
          ))}
          {address?.state_code ? (
            <span className="mt-1 block text-xs text-ink-500">State code {address.state_code}</span>
          ) : null}
        </address>
      ) : (
        <p className="mt-1 text-sm text-ink-400">Not set</p>
      )}
    </div>
  );
}
