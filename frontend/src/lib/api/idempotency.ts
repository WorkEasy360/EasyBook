/**
 * Idempotency keys for critical writes (spec §73).
 *
 * backend/core/idempotency.py replays the stored response when it sees the
 * same key AND the same body hash on the same path, so a retried request
 * cannot post a second invoice, payment or stock adjustment.
 *
 * The key must be stable per SUBMISSION, not per attempt — regenerating it on
 * retry defeats the entire mechanism. Hold one in a ref for the lifetime of a
 * form submission (see useIdempotentMutation) and only mint a new one once
 * the previous submission has definitively succeeded.
 */

export function newIdempotencyKey(): string {
  // randomUUID exists in Node 20 and in every browser secure context, which
  // includes http://localhost. The fallback covers a page served over plain
  // http from a non-local host, where the Web Crypto API is partly withheld.
  const webCrypto: Partial<Crypto> | undefined = globalThis.crypto;
  if (typeof webCrypto?.randomUUID === "function") return webCrypto.randomUUID();

  if (typeof webCrypto?.getRandomValues === "function") {
    const bytes = webCrypto.getRandomValues(new Uint8Array(16));
    return Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
  }

  throw new Error("No secure random source is available to generate an idempotency key.");
}

/** Writes worth spending a key on — every one of them moves the ledger. */
export const IDEMPOTENT_ACTIONS = [
  "invoice.post",
  "invoice.void",
  "payment.record",
  "credit-note.issue",
  "bill.post",
  "bill.void",
  "vendor-payment.record",
  "vendor-credit.issue",
  "expense.post",
  "journal.post",
  "journal.reverse",
  "stock-adjustment.post",
  "bank.reconcile",
  "bank.transfer",
] as const;

export type IdempotentAction = (typeof IDEMPOTENT_ACTIONS)[number];
