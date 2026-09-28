import { describe, expect, it } from "vitest";
import { THEME_COOKIE, parseThemePreference, themeCookie } from "./theme";

describe("parseThemePreference", () => {
  it("accepts the two explicit themes", () => {
    expect(parseThemePreference("light")).toBe("light");
    expect(parseThemePreference("dark")).toBe("dark");
  });

  it("treats anything else as following the OS", () => {
    expect(parseThemePreference(undefined)).toBe("system");
    expect(parseThemePreference(null)).toBe("system");
    expect(parseThemePreference("")).toBe("system");
    expect(parseThemePreference("system")).toBe("system");
    expect(parseThemePreference("DARK")).toBe("system");
    expect(parseThemePreference("dark; Path=/")).toBe("system");
  });
});

describe("themeCookie", () => {
  it("persists an explicit choice for a year, Lax, Secure over https", () => {
    expect(themeCookie("dark", true)).toBe(`${THEME_COOKIE}=dark; Path=/; Max-Age=31536000; SameSite=Lax; Secure`);
  });

  it("omits Secure on a plain-http origin so the cookie is actually stored", () => {
    expect(themeCookie("light", false)).toBe(`${THEME_COOKIE}=light; Path=/; Max-Age=31536000; SameSite=Lax`);
  });

  it("clears the cookie for system, so absence means follow the OS", () => {
    expect(themeCookie("system", true)).toBe(`${THEME_COOKIE}=; Path=/; Max-Age=0; SameSite=Lax; Secure`);
  });
});
