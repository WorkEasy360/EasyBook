import { errorCodeOf, fieldErrorsOf } from "@/lib/api/errors";

/**
 * Where a rejected purchase-form submission should be shown.
 *
 * Two shapes reach a form from this backend:
 *  - DRF validation errors, keyed by the WRITE field name (`amount`,
 *    `payable_account_id`), mapped straight onto inputs of the same name;
 *  - domain errors from the service layer (ApplicationError), which carry no
 *    field — just a stable `code` such as `duplicate_vendor_code` or
 *    `vendor_advance_account_required`. `codeFields` routes the codes a form
 *    knows about to the input the user must change, so the message sits next
 *    to the cause instead of only in the banner above the form.
 *
 * Returns [field, message] pairs for the caller's `setError`; anything it
 * cannot place is left to the form-level FormError.
 */
export function serverFieldErrors(
  error: unknown,
  knownFields: readonly string[],
  codeFields: Readonly<Record<string, string>> = {},
): Array<[string, string]> {
  const pairs: Array<[string, string]> = [];
  for (const [field, messages] of Object.entries(fieldErrorsOf(error))) {
    if (knownFields.includes(field) && messages[0]) pairs.push([field, messages[0]]);
  }
  const code = errorCodeOf(error);
  const target = code ? codeFields[code] : undefined;
  if (target && knownFields.includes(target) && error instanceof Error) {
    pairs.push([target, error.message]);
  }
  return pairs;
}
