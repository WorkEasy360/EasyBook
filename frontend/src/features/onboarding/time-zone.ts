/**
 * True when the runtime can format dates in this IANA zone.
 *
 * Checked by construction rather than against Intl.supportedValuesOf, whose
 * list differs by engine (Node 20's ICU lists "Asia/Calcutta" but not
 * "Asia/Kolkata", which it nonetheless accepts). Every date the app renders
 * for an organization goes through Intl with its zone, so a zone Intl rejects
 * would break the whole app for that organization.
 */
export function isValidTimeZone(value: string): boolean {
  const zone = value.trim();
  if (zone === "" || zone.length > 64) return false;
  try {
    new Intl.DateTimeFormat("en-US", { timeZone: zone });
    return true;
  } catch {
    return false;
  }
}
