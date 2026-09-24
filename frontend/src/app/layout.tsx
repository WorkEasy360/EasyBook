import type { Metadata, Viewport } from "next";
import { connection } from "next/server";
import { cookies, headers } from "next/headers";
import { ThemeProvider } from "@/components/providers/theme-provider";
import { SYSTEM_THEME_SCRIPT, THEME_COOKIE, parseThemePreference } from "@/lib/theme";
import "./globals.css";

export const metadata: Metadata = {
  title: {
    default: "EasyBook",
    template: "%s · EasyBook",
  },
  description: "Accounting, GST and inventory for growing businesses.",
  // The app is entirely behind a login; there is nothing here to index.
  robots: { index: false, follow: false },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  // The page surfaces (globals.css: ink-50 in each theme). Static metadata can
  // only follow the OS, not the stored choice; the browser chrome tint is the
  // only thing this affects.
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#ffffff" },
    { media: "(prefers-color-scheme: dark)", color: "#0f1013" },
  ],
};

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  // Every page is rendered per request. The CSP nonce (src/proxy.ts) can only
  // be applied during a request-time render; a page prerendered at build time
  // — the 404 page, say — would ship scripts with no nonce and be blocked.
  // Almost every route reads cookies and is dynamic already; this makes the
  // few that do not match.
  await connection();

  const [cookieStore, headerStore] = await Promise.all([cookies(), headers()]);
  const preference = parseThemePreference(cookieStore.get(THEME_COOKIE)?.value);
  const nonce = headerStore.get("x-nonce") ?? undefined;

  return (
    // An explicit choice is rendered here, so the first byte of CSS already
    // has the right theme. "system" leaves it unset and the inline script
    // below resolves the OS preference before anything paints — it runs
    // before React hydrates this element, hence suppressHydrationWarning.
    <html lang="en" data-theme={preference === "system" ? undefined : preference} suppressHydrationWarning>
      <body className="min-h-dvh antialiased">
        {preference === "system" ? (
          // The one inline script in the app. It carries the request's CSP
          // nonce; without it, 'strict-dynamic' would refuse to run it.
          <script nonce={nonce} dangerouslySetInnerHTML={{ __html: SYSTEM_THEME_SCRIPT }} />
        ) : null}
        <ThemeProvider initial={preference}>{children}</ThemeProvider>
      </body>
    </html>
  );
}
