import type { Route } from "next";

/**
 * The one place a runtime-computed href becomes a typed route.
 *
 * With `typedRoutes` on, Next validates LITERAL hrefs at build time, but an
 * href assembled at runtime — a filter chip's `?status=paid`, a paging link, a
 * table row's `/sales/invoices/<id>` — is just a `string` to the compiler and
 * needs `as Route` (Next 16 docs, "Statically Typed Links"). Casting here,
 * instead of at thirty call sites, keeps the escape hatch visible and
 * greppable.
 *
 * What the cast gives up is covered elsewhere: src/lib/routes.test.ts scans
 * the source for every internal link pattern and fails if one points at a
 * page that does not exist.
 */
export function appHref(href: string): Route {
  return href as Route;
}
