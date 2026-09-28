import { describe, expect, it } from "vitest";
import { ApiError } from "@/lib/api/errors";
import { serverFieldErrors } from "./form-errors";

describe("serverFieldErrors", () => {
  it("maps DRF field errors onto known inputs only", () => {
    const error = new ApiError({
      status: 400,
      message: "Request failed validation.",
      code: { amount: ["invalid"] },
      details: { amount: ["A valid number is required."], lines: [{ quantity: ["Required."] }] },
    });
    expect(serverFieldErrors(error, ["amount", "payment_date"])).toEqual([
      ["amount", "A valid number is required."],
    ]);
  });

  it("routes a domain error code to the input that caused it", () => {
    const error = new ApiError({
      status: 400,
      message: "A vendor with code 'V-1' already exists.",
      code: "duplicate_vendor_code",
    });
    expect(serverFieldErrors(error, ["vendor_code"], { duplicate_vendor_code: "vendor_code" })).toEqual([
      ["vendor_code", "A vendor with code 'V-1' already exists."],
    ]);
  });

  it("leaves unknown codes to the form-level message", () => {
    const error = new ApiError({ status: 400, message: "No fiscal year covers this date.", code: "fiscal_year_missing" });
    expect(serverFieldErrors(error, ["bill_date"], { duplicate_vendor_code: "vendor_code" })).toEqual([]);
  });

  it("ignores anything that is not an API error", () => {
    expect(serverFieldErrors(new Error("offline"), ["amount"], { offline: "amount" })).toEqual([]);
  });
});
