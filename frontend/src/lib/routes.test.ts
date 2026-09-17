import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative, sep } from "node:path";
import { describe, expect, it } from "vitest";
import { NAVIGATION, blockedRoutes } from "./navigation";

/**
 * No broken internal links (spec §102).
 *
 * `typedRoutes` validates literal hrefs at build time, but most links in this
 * app are assembled at runtime — table rows, filters, "create from" buttons —
 * and pass through appHref() (src/lib/routes.ts), which the compiler cannot
 * check. This test closes that gap: it collects every app-path string in the
 * source (literals and template literals, with `${…}` standing in for a
 * dynamic segment) and requires each one to resolve to a real page under
 * src/app, the way the App Router would match it.
 */

const SRC = join(process.cwd(), "src");
const APP = join(SRC, "app");

/** Top-level sections that are pages (not API calls, which share names like "sales/invoices"). */
const APP_SECTIONS = [
  "dashboard", "items", "inventory", "sales", "purchases", "projects", "banking", "accounting",
  "tax", "reports", "documents", "automation", "ai", "settings", "onboarding", "login", "register",
];

function walk(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) walk(path, out);
    else out.push(path);
  }
  return out;
}

/** Page routes as segment arrays, with route groups removed: "(app)/sales/invoices/[id]". */
function pageRoutes(): string[][] {
  return walk(APP)
    .filter((file) => /[\\/]page\.tsx$/.test(file))
    .map((file) =>
      relative(APP, file)
        .split(sep)
        .slice(0, -1)
        .filter((segment) => !(segment.startsWith("(") && segment.endsWith(")"))),
    );
}

const ROUTES = pageRoutes();

function resolves(path: string): boolean {
  const segments = path.split("/").filter(Boolean);
  return ROUTES.some(
    (route) =>
      route.length === segments.length &&
      route.every((part, index) => {
        const segment = segments[index] ?? "";
        if (part.startsWith("[") && part.endsWith("]")) return segment.length > 0;
        // A template placeholder is a dynamic value; it can only fill a dynamic segment.
        return segment === part;
      }),
  );
}

/** Every "/section/..." path written in the source, normalised. */
function linkedPaths(): Array<{ path: string; file: string }> {
  const found: Array<{ path: string; file: string }> = [];
  const sections = APP_SECTIONS.join("|");
  // A string or template literal that starts with "/<section>" — an in-app path.
  const pattern = new RegExp("[\"'`](/(?:" + sections + ")(?:[/?#][^\"'`\\s]*)?)[\"'`]", "g");

  for (const file of walk(SRC)) {
    if (!/\.(tsx?|ts)$/.test(file) || /\.test\.tsx?$/.test(file)) continue;
    if (file.includes(`${sep}app${sep}api${sep}`)) continue;
    const source = readFileSync(file, "utf8");
    for (const match of source.matchAll(pattern)) {
      const raw = match[1] ?? "";
      // Strip query/hash, then replace each ${…} with a placeholder segment value.
      const withoutQuery = raw.replace(/\$\{[^}]*\}/g, "__dynamic__").split(/[?#]/)[0] ?? "";
      const path = withoutQuery.replace(/\/+$/, "") || "/";
      // A path that ENDS in a template expression prefix ("/sales/invoices/" + id)
      // is written as "/sales/invoices/${id}" and handled above; a bare prefix
      // used for string concatenation ("/reports/") is not a link on its own.
      if (raw.endsWith("/") && !raw.includes("${")) continue;
      found.push({ path, file: relative(SRC, file) });
    }
  }
  return found;
}

describe("internal links", () => {
  const blocked = new Set(blockedRoutes().map((route) => route.href));

  it("finds the links it is meant to check", () => {
    // Guards the scanner itself: if the pattern silently matched nothing,
    // every other assertion here would pass vacuously.
    expect(linkedPaths().length).toBeGreaterThan(100);
  });

  it("every in-app path in the source resolves to a page", () => {
    const broken = linkedPaths()
      .filter(({ path }) => !path.startsWith("/api/"))
      // Recorded in navigation.ts as having no backend API; never rendered.
      .filter(({ path }) => !blocked.has(path))
      .filter(({ path }) => !resolves(path.replace(/__dynamic__/g, "x")))
      .map(({ path, file }) => `${path}  (${file})`);
    expect([...new Set(broken)]).toEqual([]);
  });

  it("every navigation entry resolves, except the ones recorded as blocked", () => {
    const destinations = NAVIGATION.flatMap((section) => [
      ...(section.href ? [section.href] : []),
      ...(section.items ?? []).map((item) => item.href),
    ]);
    const broken = destinations.filter((href) => !blocked.has(href) && !resolves(href));
    expect(broken).toEqual([]);
  });

  it("no blocked route has quietly gained a page (the blocker should be revisited)", () => {
    const built = [...blocked].filter((href) => resolves(href));
    expect(built).toEqual([]);
  });
});
