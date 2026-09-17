import { describe, expect, it } from "vitest";
import { isValidTimeZone } from "./time-zone";
import { monthName } from "@/features/settings/months";

describe("isValidTimeZone", () => {
  it("accepts IANA zones, including aliases some ICU builds do not list", () => {
    expect(isValidTimeZone("Asia/Kolkata")).toBe(true);
    expect(isValidTimeZone("UTC")).toBe(true);
    expect(isValidTimeZone(" Europe/London ")).toBe(true);
  });

  it("rejects what would break date rendering for the organization", () => {
    expect(isValidTimeZone("")).toBe(false);
    expect(isValidTimeZone("India")).toBe(false);
    expect(isValidTimeZone("Asia/Nowhere")).toBe(false);
  });
});

describe("monthName", () => {
  it("names months 1–12 and nothing else", () => {
    expect(monthName(4)).toBe("April");
    expect(monthName(12)).toBe("December");
    expect(monthName(0)).toBe("—");
    expect(monthName(13)).toBe("—");
  });
});
