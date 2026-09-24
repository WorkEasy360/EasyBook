"use client";

import * as React from "react";
import { themeCookie, type ThemePreference } from "@/lib/theme";

interface ThemeContextValue {
  preference: ThemePreference;
  setPreference: (next: ThemePreference) => void;
}

const ThemeContext = React.createContext<ThemeContextValue | null>(null);

/**
 * Owns the theme preference on the client.
 *
 * The root layout has already rendered <html data-theme> from the cookie (or
 * let its inline script resolve the OS preference), so first paint is right
 * before this mounts. Its job is what happens afterwards: a pick in the
 * account menu, or the OS switching while "system" is selected.
 */
export function ThemeProvider({ initial, children }: { initial: ThemePreference; children: React.ReactNode }) {
  const [preference, setPreferenceState] = React.useState<ThemePreference>(initial);

  React.useEffect(() => {
    const root = document.documentElement;
    if (preference !== "system") {
      root.dataset.theme = preference;
      return;
    }
    const query = window.matchMedia("(prefers-color-scheme: dark)");
    const follow = () => {
      root.dataset.theme = query.matches ? "dark" : "light";
    };
    follow();
    query.addEventListener("change", follow);
    return () => query.removeEventListener("change", follow);
  }, [preference]);

  const setPreference = React.useCallback((next: ThemePreference) => {
    setPreferenceState(next);
    // Written by the browser, read by the next server render (src/app/layout.tsx).
    document.cookie = themeCookie(next, window.location.protocol === "https:");
  }, []);

  const value = React.useMemo(() => ({ preference, setPreference }), [preference, setPreference]);

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme(): ThemeContextValue {
  const context = React.useContext(ThemeContext);
  if (!context) throw new Error("useTheme() must be used inside <ThemeProvider>.");
  return context;
}
