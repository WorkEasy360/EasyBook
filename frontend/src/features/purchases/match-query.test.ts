import { describe, expect, it } from "vitest";
import { toleranceQuery } from "./match-query";

describe("toleranceQuery", () => {
  it("passes valid tolerances through as decimal strings", () => {
    expect(toleranceQuery({ quantity_tolerance: "0.5", price_tolerance: "10.25" })).toEqual({
      quantity_tolerance: "0.5",
      price_tolerance: "10.25",
    });
  });

  it("drops values the serializer would reject", () => {
    expect(
      toleranceQuery({ quantity_tolerance: "-1", price_tolerance: "1.005", page: "2" }),
    ).toEqual({});
    expect(toleranceQuery({ quantity_tolerance: "abc", price_tolerance: "" })).toEqual({});
  });

  it("uses the first value of a repeated param", () => {
    expect(toleranceQuery({ price_tolerance: ["5", "9"] })).toEqual({ price_tolerance: "5" });
  });
});
