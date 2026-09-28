import type { ApiErrorCode, ApiErrorEnvelope, FieldErrors } from "./types";

/**
 * Every non-2xx response becomes one of these. Feature code should never see
 * a raw Response or a bare `fetch` rejection.
 */
export class ApiError extends Error {
  readonly status: number;
  readonly code: ApiErrorCode;
  readonly details: unknown;
  readonly requestId: string | null;

  constructor(init: {
    status: number;
    message: string;
    code?: ApiErrorCode;
    details?: unknown;
    requestId?: string | null;
  }) {
    super(init.message);
    this.name = "ApiError";
    this.status = init.status;
    this.code = init.code ?? "error";
    this.details = init.details ?? null;
    this.requestId = init.requestId ?? null;
  }

  get isUnauthorized(): boolean {
    return this.status === 401;
  }

  get isForbidden(): boolean {
    return this.status === 403;
  }

  get isNotFound(): boolean {
    return this.status === 404;
  }

  get isConflict(): boolean {
    return this.status === 409;
  }

  get isValidation(): boolean {
    return this.status === 400 || this.status === 422;
  }

  get isRateLimited(): boolean {
    return this.status === 429;
  }

  get isServer(): boolean {
    return this.status >= 500;
  }

  /**
   * True when the tenant header was missing or named an organization the user
   * cannot reach — backend/core/views.py :: resolve_membership. Worth
   * distinguishing from a plain 403 because the fix is "switch organization",
   * not "ask for permission".
   */
  get isOrganizationError(): boolean {
    return (
      errorCodeOf(this) === "organization_required" ||
      errorCodeOf(this) === "organization_forbidden"
    );
  }
}

/** Raised when the request never reached the server (offline, DNS, timeout). */
export class NetworkError extends Error {
  constructor(message: string, cause?: unknown) {
    // Error's own `cause` option — no shadowing field, which would need an
    // `override` modifier and lose the built-in behaviour.
    super(message, { cause });
    this.name = "NetworkError";
  }
}

/** Raised when a request exceeded its own deadline. */
export class TimeoutError extends NetworkError {
  constructor(ms: number) {
    super(`The request took longer than ${Math.round(ms / 1000)}s and was cancelled.`);
    this.name = "TimeoutError";
  }
}

export function isApiError(error: unknown): error is ApiError {
  return error instanceof ApiError;
}

export function isNetworkError(error: unknown): error is NetworkError {
  return error instanceof NetworkError;
}

/** Narrows the possibly-nested DRF code to a string, or null if it is not one. */
export function errorCodeOf(error: unknown): string | null {
  if (!isApiError(error)) return null;
  return typeof error.code === "string" ? error.code : null;
}

function isEnvelope(body: unknown): body is ApiErrorEnvelope {
  if (typeof body !== "object" || body === null || !("error" in body)) return false;
  const inner = (body as { error: unknown }).error;
  return typeof inner === "object" && inner !== null && "message" in inner;
}

/**
 * Turns a failed response body into an ApiError. Falls back to a status-based
 * message when the body is not the documented envelope — an ALB 502 or a WAF
 * block returns HTML, not JSON, and must not crash the client.
 */
export function toApiError(
  status: number,
  body: unknown,
  fallbackRequestId: string | null,
): ApiError {
  if (isEnvelope(body)) {
    return new ApiError({
      status,
      message: body.error.message,
      code: body.error.code,
      details: body.error.details,
      // `||` not `??`: the envelope can carry an empty string, and an
      // empty id is no id — fall back to the X-Request-ID header.
      requestId: body.error.request_id || fallbackRequestId,
    });
  }
  return new ApiError({
    status,
    message: defaultMessageForStatus(status),
    details: null,
    requestId: fallbackRequestId,
  });
}

/**
 * User-facing copy for a status with no usable envelope. Never leaks a stack
 * trace or an upstream body (spec §15).
 */
export function defaultMessageForStatus(status: number): string {
  switch (status) {
    case 400:
      return "That request could not be processed. Please check the details and try again.";
    case 401:
      return "Your session has expired. Please sign in again.";
    case 403:
      return "You do not have permission to do that.";
    case 404:
      return "That record could not be found.";
    case 409:
      return "This record changed since you loaded it. Reload and try again.";
    case 413:
      return "That file is too large to upload.";
    case 422:
      return "Some of the information provided is not valid.";
    case 429:
      return "Too many requests. Please wait a moment and try again.";
    case 503:
      return "EasyBook is temporarily unavailable. Please try again shortly.";
    default:
      return status >= 500
        ? "Something went wrong on our end. Please try again."
        : "That request could not be completed.";
  }
}

/**
 * Extracts `field -> messages` from a DRF ValidationError body so a form can
 * attach server errors to the right inputs. Returns {} for any other shape.
 */
export function fieldErrorsOf(error: unknown): FieldErrors {
  if (!isApiError(error) || !error.isValidation) return {};
  const details = error.details;
  if (typeof details !== "object" || details === null || Array.isArray(details)) return {};

  const out: FieldErrors = {};
  for (const [key, value] of Object.entries(details as Record<string, unknown>)) {
    const messages = normalizeMessages(value);
    if (messages.length > 0) out[key] = messages;
  }
  return out;
}

function normalizeMessages(value: unknown): string[] {
  if (typeof value === "string") return [value];
  if (Array.isArray(value)) {
    return value.flatMap((entry) => normalizeMessages(entry));
  }
  if (typeof value === "object" && value !== null) {
    // Nested serializer (e.g. invoice `lines`): flatten one level so the
    // message still reaches the user even if it cannot reach a named input.
    return Object.values(value as Record<string, unknown>).flatMap(normalizeMessages);
  }
  return [];
}

/**
 * DRF puts non-field problems under `non_field_errors`; surface those as the
 * form-level message rather than the generic "Request failed validation."
 */
export function formErrorOf(error: unknown): string | null {
  if (!isApiError(error)) return null;
  const fields = fieldErrorsOf(error);
  const nonField = fields["non_field_errors"] ?? fields["detail"];
  if (nonField && nonField.length > 0) return nonField.join(" ");
  return error.message;
}

/** "Reference: req_..." text for support, per spec §15. Null when absent. */
export function referenceOf(error: unknown): string | null {
  if (!isApiError(error) || !error.requestId) return null;
  return error.requestId;
}
