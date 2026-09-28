import { describe, expect, it } from "vitest";
import {
  type ApiError,
  defaultMessageForStatus,
  errorCodeOf,
  fieldErrorsOf,
  formErrorOf,
  referenceOf,
  toApiError,
} from "./errors";

/** Exactly what backend/core/exceptions.py :: api_exception_handler emits. */
function envelope(code: unknown, message: string, details: unknown = null) {
  return { error: { code, message, details, request_id: "req-abc-123" } };
}

describe("envelope normalization", () => {
  it("reads the documented error envelope", () => {
    const error = toApiError(400, envelope("invalid", "Request failed validation."), null);
    expect(error.status).toBe(400);
    expect(error.message).toBe("Request failed validation.");
    expect(error.code).toBe("invalid");
    expect(error.requestId).toBe("req-abc-123");
  });

  it("falls back to a safe message when the body is not the envelope", () => {
    // An ALB 502 or a WAF block returns HTML, not JSON. It must not crash the
    // client or surface raw upstream markup.
    const error = toApiError(502, "<html>Bad Gateway</html>", "req-9");
    expect(error.message).toBe("Something went wrong on our end. Please try again.");
    expect(error.requestId).toBe("req-9");
    expect(error.isServer).toBe(true);
  });

  it("survives a null body", () => {
    const error = toApiError(500, null, null);
    expect(error.status).toBe(500);
    expect(error.message).toBeTruthy();
  });

  it("prefers the envelope request id over the header one", () => {
    const error = toApiError(400, envelope("invalid", "Nope."), "header-id");
    expect(error.requestId).toBe("req-abc-123");
  });

  it("uses the header request id when the envelope omits it", () => {
    const error = toApiError(
      400,
      { error: { code: "invalid", message: "Nope.", details: null, request_id: "" } },
      "header-id",
    );
    expect(error.requestId).toBe("header-id");
  });
});

describe("status classification", () => {
  const cases: Array<[number, keyof ApiError]> = [
    [401, "isUnauthorized"],
    [403, "isForbidden"],
    [404, "isNotFound"],
    [409, "isConflict"],
    [429, "isRateLimited"],
    [500, "isServer"],
  ];

  it.each(cases)("classifies %i", (status, flag) => {
    const error = toApiError(status, envelope("x", "y"), null);
    expect(error[flag]).toBe(true);
  });

  it("treats both 400 and 422 as validation", () => {
    expect(toApiError(400, envelope("invalid", "x"), null).isValidation).toBe(true);
    expect(toApiError(422, envelope("invalid", "x"), null).isValidation).toBe(true);
  });

  it("recognises the tenant-resolution failures as organization errors", () => {
    // backend/core/views.py :: resolve_membership. Distinct from a plain 403
    // because the fix is "switch organization", not "ask for permission".
    const missing = toApiError(400, envelope("organization_required", "x"), null);
    const foreign = toApiError(403, envelope("organization_forbidden", "x"), null);
    expect(missing.isOrganizationError).toBe(true);
    expect(foreign.isOrganizationError).toBe(true);

    const ordinary = toApiError(403, envelope("permission_denied", "x"), null);
    expect(ordinary.isOrganizationError).toBe(false);
  });

  it("returns null for a non-string DRF code rather than guessing", () => {
    // ValidationError's get_codes() returns a nested structure, not a string.
    const error = toApiError(400, envelope({ email: ["required"] }, "x"), null);
    expect(errorCodeOf(error)).toBeNull();
  });
});

describe("field errors", () => {
  it("maps DRF field errors to per-input messages", () => {
    const error = toApiError(
      400,
      envelope("invalid", "Request failed validation.", {
        email: ["Enter a valid email address."],
        invoice_date: ["This field is required."],
      }),
      null,
    );
    expect(fieldErrorsOf(error)).toEqual({
      email: ["Enter a valid email address."],
      invoice_date: ["This field is required."],
    });
  });

  it("flattens a nested serializer so the message still reaches the user", () => {
    // Invoice `lines` errors cannot attach to a named input, but losing them
    // entirely would leave the user staring at a rejected form with no reason.
    const error = toApiError(
      400,
      envelope("invalid", "Request failed validation.", {
        lines: { 0: { quantity: ["Must be greater than zero."] } },
      }),
      null,
    );
    expect(fieldErrorsOf(error)["lines"]).toEqual(["Must be greater than zero."]);
  });

  it("accepts a bare string as well as a list", () => {
    const error = toApiError(400, envelope("invalid", "x", { detail: "Nope." }), null);
    expect(fieldErrorsOf(error)["detail"]).toEqual(["Nope."]);
  });

  it("returns nothing for a non-validation error", () => {
    expect(fieldErrorsOf(toApiError(500, envelope("error", "x"), null))).toEqual({});
  });

  it("surfaces non_field_errors as the form-level message", () => {
    const error = toApiError(
      400,
      envelope("invalid", "Request failed validation.", {
        non_field_errors: ["Debits and credits must balance."],
      }),
      null,
    );
    expect(formErrorOf(error)).toBe("Debits and credits must balance.");
  });

  it("falls back to the envelope message when there is no non-field error", () => {
    const error = toApiError(400, envelope("invalid", "Something specific."), null);
    expect(formErrorOf(error)).toBe("Something specific.");
  });
});

describe("support reference", () => {
  it("exposes the request id for the 'Reference: …' line", () => {
    expect(referenceOf(toApiError(500, envelope("error", "x"), null))).toBe("req-abc-123");
  });

  it("returns null when there is no id and for non-API errors", () => {
    expect(referenceOf(new Error("boom"))).toBeNull();
    expect(referenceOf(toApiError(500, "<html>", null))).toBeNull();
  });
});

describe("user-facing copy", () => {
  it("never leaks internals for any status", () => {
    for (const status of [400, 401, 403, 404, 409, 413, 422, 429, 500, 502, 503]) {
      const message = defaultMessageForStatus(status);
      expect(message).not.toMatch(/traceback|exception|django|postgres/i);
      expect(message.length).toBeGreaterThan(0);
    }
  });
});
