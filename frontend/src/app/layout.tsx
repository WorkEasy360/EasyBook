import type { Metadata, Viewport } from "next";
import { connection } from "next/server";
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
  themeColor: "#ffffff",
};

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  // Every page is rendered per request. The CSP nonce (src/proxy.ts) can only
  // be applied during a request-time render; a page prerendered at build time
  // — the 404 page, say — would ship scripts with no nonce and be blocked.
  // Almost every route reads cookies and is dynamic already; this makes the
  // few that do not match.
  await connection();

  return (
    <html lang="en">
      <body className="min-h-dvh antialiased">{children}</body>
    </html>
  );
}
