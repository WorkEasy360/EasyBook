import { describe, expect, it } from "vitest";
import { EMPTY_REGISTER_VALUES, isServerField, registerSchema, type RegisterValues } from "./register-schema";

const valid: RegisterValues = {
  ...EMPTY_REGISTER_VALUES,
  first_name: "Asha",
  email: "asha@example.com",
  password: "ledger-lantern-42",
  confirm_password: "ledger-lantern-42",
};

function issuesFor(values: RegisterValues): Record<string, string> {
  const result = registerSchema.safeParse(values);
  if (result.success) return {};
  return Object.fromEntries(result.error.issues.map((issue) => [issue.path.join("."), issue.message]));
}

describe("registerSchema", () => {
  it("accepts a complete sign-up, with names optional", () => {
    expect(registerSchema.safeParse(valid).success).toBe(true);
    expect(registerSchema.safeParse({ ...valid, first_name: "", last_name: "" }).success).toBe(true);
  });

  it("mirrors the backend's minimum length of 10", () => {
    expect(issuesFor({ ...valid, password: "short-pw1", confirm_password: "short-pw1" })).toHaveProperty("password");
    expect(issuesFor({ ...valid, password: "exactly-10", confirm_password: "exactly-10" })).toEqual({});
  });

  it("rejects an all-number password", () => {
    expect(issuesFor({ ...valid, password: "12345678901", confirm_password: "12345678901" })).toHaveProperty(
      "password",
    );
  });

  it("reports a mismatched confirmation on the confirmation field", () => {
    expect(issuesFor({ ...valid, confirm_password: "something-else" })).toEqual({
      confirm_password: "The passwords do not match.",
    });
  });

  it("requires a valid email", () => {
    expect(issuesFor({ ...valid, email: "not-an-email" })).toHaveProperty("email");
  });
});

describe("isServerField", () => {
  it("maps backend fields that have inputs, and nothing else", () => {
    expect(isServerField("email")).toBe(true);
    expect(isServerField("password")).toBe(true);
    expect(isServerField("non_field_errors")).toBe(false);
    expect(isServerField("confirm_password")).toBe(false);
  });
});
