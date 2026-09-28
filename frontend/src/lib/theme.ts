/**
 * Theme preference: light, dark, or follow the OS.
 *
 * The choice lives in a plain cookie rather than localStorage so the SERVER
 * can stamp `data-theme` on <html> before any CSS loads — no flash of the
 * wrong theme and no client-side re-render to fix it. "system" is the absence
 * of the cookie: the root layout then lets a nonce'd inline script resolve
 * the OS preference before first paint (src/app/layout.tsx), and the provider
 * follows OS changes live. Not httpOnly — it is a UI preference the browser
 * itself writes — and it never travels upstream: the BFF forwards no cookies.
 *
 * The theme itself is a token swap in src/app/globals.css.
 */

export const THEME_COOKIE = "eb_theme";
export const THEME_PREFERENCES = ["light", "dark", "system"] as const;
export type ThemePreference = (typeof THEME_PREFERENCES)[number];

const ONE_YEAR_SECONDS = 365 * 24 * 60 * 60;

/** Anything but an explicit light/dark — missing, empty, tampered — is "system". */
export function parseThemePreference(value: string | null | undefined): ThemePreference {
  return value === "light" || value === "dark" ? value : "system";
}

/** The `document.cookie` assignment for a choice; "system" clears the cookie. */
export function themeCookie(preference: ThemePreference, secure: boolean): string {
  const explicit = preference !== "system";
  const value = explicit ? preference : "";
  const maxAge = explicit ? ONE_YEAR_SECONDS : 0;
  return `${THEME_COOKIE}=${value}; Path=/; Max-Age=${maxAge}; SameSite=Lax${secure ? "; Secure" : ""}`;
}

/**
 * Runs before first paint when no preference is stored. Static, so it can be
 * inlined under the CSP nonce; it only ever touches the html element's
 * data-theme, and only when the server left it unset.
 */
export const SYSTEM_THEME_SCRIPT =
  '(function(){try{var d=document.documentElement;if(!d.dataset.theme){d.dataset.theme=matchMedia("(prefers-color-scheme: dark)").matches?"dark":"light"}}catch(e){}})()';
