import type { NextConfig } from "next";

/**
 * The browser never talks to Django directly — it calls the same-origin BFF
 * route handlers under /api/bff, which attach the access token from an
 * httpOnly cookie (src/lib/auth/session.ts). That is why there is no
 * NEXT_PUBLIC_API_URL here: a public API base would invite feature code to
 * fetch Django from the browser and reintroduce token-in-JS storage.
 */
const nextConfig: NextConfig = {
  reactStrictMode: true,
  /*
   * Dev only. Next 16 blocks cross-origin dev-resource requests by
   * default, and browsing the dev server on 127.0.0.1 counts as
   * cross-origin — which blocks the HMR/client bundle and leaves pages
   * unhydrated. Has no effect on a production build.
   */
  allowedDevOrigins: ["127.0.0.1", "localhost"],
  poweredByHeader: false,
  /*
   * ON: typedRoutes makes a <Link> to a route that does not exist a BUILD
   * error — the "no broken routes" guarantee the production gate relies on
   * (spec §102). Dynamic hrefs built from strings are checked as `Route`
   * casts at their call sites.
   */
  typedRoutes: true,
  experimental: {
    // Recharts and the icon set are the two heaviest client imports; barrel
    // optimization keeps a single chart from pulling the whole library.
    optimizePackageImports: ["recharts"],
  },
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          { key: "X-Frame-Options", value: "DENY" },
          {
            key: "Permissions-Policy",
            value: "camera=(), microphone=(), geolocation=(), payment=()",
          },
        ],
      },
    ];
  },
};

export default nextConfig;
