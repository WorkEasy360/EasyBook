# FRONTEND

PURPOSE
Production UI for EasyBook, consuming the `backend/` REST API under `/api/v1/`.
Original design system — never a clone of Zoho Books/Xero/QuickBooks/Tally.

STACK (verified against the registry, not assumed — see "VERSION TRAPS")
Next.js 16.3.5 (App Router, Turbopack) · React 19.3.0 · TypeScript 5.9.3 ·
Tailwind 4.3.3 · TanStack Query 5 · react-hook-form 7 + zod 4 · big.js 7 ·
Vitest 4 · Playwright 1.63. Node 20.9+. All dependencies pinned exactly.

RUNNING LOCALLY
`npm run dev` (needs `API_BASE_URL`, see `.env.example`). The backend must be
running — `infrastructure/docker-compose.yml` for Postgres/Redis, then
`make -C backend run` (Django on **8001**, not the default 8000 — another
project on this machine holds 8000; override with `make -C backend run
RUN_PORT=...`). The raw form is
`backend/.venv/Scripts/python.exe manage.py runserver 127.0.0.1:8001`
(the venv's python — a global/conda `python` lacks the dependencies).
If Postgres/Redis are not up, manage.py hangs on the DB connect instead of
printing an error.
Open `http://localhost:3000`, not the "Network" LAN address `next dev`
prints: Next 16 blocks its dev resources for any other host unless it is in
`allowedDevOrigins`, so the page never hydrates — forms then fall back to a
native POST to the page itself and nothing happens.
`npm run check` = typecheck + lint + unit tests, in fail-fastest order.
Lint bar is 0 errors AND 0 warnings (`npx eslint . --max-warnings 0`).
E2E: `E2E_BASE_URL=http://127.0.0.1:3000 npx playwright test --workers=1` —
one worker, because every spec signs in as the same user. The backend
throttles per user (300/min) and sign-in (20/min); a full run against a
production build is fast enough to hit both, so start the E2E backend with
`THROTTLE_USER=20000/min THROTTLE_AUTH=2000/min` (test environment only) and
pass `E2E_API_URL` when Django is not on :8001. Run the suite against
`next build && next start` before release — two production-only failures
(a CSP https upgrade, a redirect loop) never reproduced under `next dev`. Backend tests must run with
`DJANGO_SETTINGS_MODULE=config.settings.test` (dev settings leave Celery
non-eager and several tests fail spuriously).

## ARCHITECTURE: THE BFF

The browser NEVER calls Django. It calls same-origin `/api/bff/*`
(`src/app/api/bff/[...path]/route.ts`), which attaches the access token from
an httpOnly cookie server-side. Consequences, all deliberate:

- No token is ever readable by JS, so an XSS cannot lift a session.
- No CORS preflight on every call, and no `connect-src` relaxation in CSP.
- The 401-refresh-retry lives in one place instead of every feature.

There is no `NEXT_PUBLIC_API_URL`. Adding one would invite direct browser
calls and reintroduce token-in-JS storage.

Two transports, one contract:
- `src/lib/api/server.ts` — Server Components / Server Actions.
- `src/lib/api/browser.ts` — Client Components, via the BFF.
Do not write a third. Features must not hand-roll `fetch`.

## TOKEN REFRESH — READ BEFORE TOUCHING AUTH

The backend sets `ROTATE_REFRESH_TOKENS` **and** `BLACKLIST_AFTER_ROTATION`.
Every refresh issues a new refresh token and blacklists the one used. Two
things follow, and both are load-bearing:

1. `src/lib/auth/refresh.ts` collapses concurrent refreshes of the same token
   into one upstream call. Without it, parallel requests blacklist each
   other's token and sign the user out at random. Verified with six parallel
   requests sharing one stale access token.
2. Refresh-before-render lives in `src/proxy.ts`, NOT in the server API
   client. A Server Component cannot set cookies — it could obtain a new pair
   but not persist it, which would blacklist the stored refresh token and end
   the session. The proxy runs before render and can set cookies.

`src/proxy.ts` is Next 16's rename of `middleware.ts`; `config.matcher` is
unchanged.

## INVARIANTS

- Backend financial values are authoritative. Never recreate the accounting,
  tax or inventory-valuation engines here. Form-time arithmetic is an
  ESTIMATE and is replaced by the server's response after save.
- **Money is a decimal STRING end to end.** Never `Number()` an amount, not
  even to display it — `Intl.NumberFormat.format` takes a string and keeps
  full precision. `src/lib/money.ts` is the only module that formats money;
  `<Money>` is the only component that renders it. ESLint blocks `parseFloat`
  everywhere else.
- A `DateString` ("2026-03-31") is a calendar date and must never pass through
  a timezone — `new Date("2026-03-31")` is UTC midnight and renders as the
  30th in western zones, moving an invoice out of its fiscal year. Only
  `DateTimeString` instants get converted, into the organization's zone.
- Permission-aware UI: `src/lib/authz/permissions.ts` is GENERATED from
  `backend/authz/roles.py` (`scripts/generate-permissions.py`). It is a UI
  hint only — Django authorizes every request. Regenerate it when roles change.
- Tenant-safe caching: the query client is keyed by organization id in
  `src/app/(app)/layout.tsx`, so switching tenants remounts and discards the
  previous cache. Organization switch and sign-out use a FULL navigation, not
  `router.push`, so no RSC payload survives.
- Every data surface ships loading, empty, error and forbidden states —
  `src/components/ui/states.tsx`.
- Critical writes send an `Idempotency-Key` generated per SUBMISSION, not per
  attempt (`src/lib/api/idempotency.ts`).

## SECURITY CONTROLS (all tested)

- CSP: nonce-based, minted per request in `src/proxy.ts` via
  `src/lib/security/csp.ts`; every page-rendering path must go through
  `pass()`. Root layout calls `await connection()` so no page is prerendered
  without a nonce. Production style-src is nonce-only; dev relaxes inline
  <style> for HMR. `e2e/security-csp.spec.ts` asserts zero violations —
  run it against `next build && next start`, not only `next dev`.
- Cross-origin writes: `src/lib/security/request-guards.ts` refuses
  state-changing requests a browser reports as cross-origin (Sec-Fetch-Site,
  then Origin/Host) on the BFF and `/api/auth/*`. Request bodies are streamed
  with a 26 MiB cap (`BFF_MAX_BODY_BYTES`).
- Sign-in and sign-up share `src/lib/auth/sign-in.ts` (credential exchange,
  cookie writes, first-organization pick). `/api/auth/register` relays the
  backend's field errors and then signs the new user in; the password rules
  are the backend's (`RegisterSerializer` runs AUTH_PASSWORD_VALIDATORS) —
  `features/auth/register-schema.ts` only mirrors length/numeric for early
  feedback.
- BFF passes null-body statuses (204/205/304) with a null body — the
  Response constructor throws otherwise (every DELETE used to 500).
- Missing organization cookie: the BFF and `serverApi` fall back to the
  user's first organization — the same one `requireSession()` renders — and
  the BFF re-sets the cookie. They must never disagree on the tenant.
- Never render API text as HTML (OCR text, memos, AI answers are plain text);
  never fetch a signed download URL from the browser (connect-src 'self') —
  open it by navigation.

## SHARED BUILDING BLOCKS (use these; do not re-create)

- Lists: `parseListQuery` + `FilterBar` + `DataTable` (Server Components,
  link-based) + `SortNote(capabilitiesFor(resource).defaultOrder)`.
  `wholeList()` renders an unpaginated report through DataTable.
- Filters/periods: `DateFilterForm` (GET form), `paramOf`/`dateParamOf`.
- Actions: `features/shared/document-action.tsx` for EVERY state transition
  (confirm dialog, idempotency key, errors stay in the dialog, refresh).
- Priced documents: `features/documents/priced-lines.ts` (pure: schema,
  mapping — import this from Server Components), `priced-lines-editor.tsx`
  (client editor + `EstimatedTotals`), `estimate.ts` (mirrors
  backend/core/money.py exactly; tested), `document-view.tsx` (lines table,
  totals card, party card), `overdue-hint.tsx`.
- Lookups: `lib/api/lookups.ts` — `recordsById` (detail pages),
  `indexList` (list pages, with per-id backfill). Never show a raw UUID.
- Links: `LinkButton`; runtime-computed hrefs go through `appHref()`
  (`src/lib/routes.ts`) because `typedRoutes` is ON. `src/lib/routes.test.ts`
  scans the source and fails on any in-app path with no page.
- CSV: only via `csvExportHref(report, params)` — null means the backend has
  no export, so render no button. No trailing slash (Next 308s it).
- Status/label maps for every module live in `components/ui/status-badge.tsx`,
  transcribed from backend TextChoices.

## API CONTRACT

- Pagination: `{count, next, previous, results}`, `?page=`, `?page_size=`
  (max 200). Several endpoints return BARE ARRAYS instead (organizations,
  members, document links, timesheet, unbilled time, /inventory/stock-summary).
- Errors: `{error: {code, message, details, request_id}}`. `code` is NOT
  always a string — use `errorCodeOf()`.
- READ serializers return relations as ids (`customer`); WRITE serializers
  usually take `_id` (`customer_id`). Types in `src/types/api/*` were
  regenerated from `scripts/dump-serializers.py` plus live captures — every
  earlier hand-written version was wrong somewhere. Verify, never guess.
- Only drafts accept PATCH, and each detail view accepts a specific set of
  header keys (read its `update()`); fields fixed after creation are shown
  disabled.
- List filters: only what `LIST_CAPABILITIES` records (extracted from each
  view's query_params). No endpoint supports `?search=`/`?ordering=` except
  the dedicated `/documents/search/`.
- Document numbers: quotes, orders, challans, payments, POs, goods receipts
  are numbered on create; invoices, bills, expenses, credit notes, vendor
  credits only on post/issue (blank until then).
- "overdue" is never a stored status; lateness is derived from due_date
  (display only — the overdue reports are authoritative).
- Reports use `warehouse`/`item`/`account` params (not `_id`); flat reports
  wrap rows in `{rows}`; GST endpoints require both from_date and to_date.
- CSV export is `?export=csv` — not `?format=`, which DRF reserves.

## BACKEND CONTRACT BLOCKERS (do not build UI on these)

CRITICAL — no API creates a FiscalYear. Every posting (invoice, bill,
journal, adjustment, payment) fails `fiscal_year_not_found` in a new
organization. Dev tenant has one created via Django shell only.

Also missing: tax/compliance/audit REST APIs (tax rates, e-Invoice, e-Way
Bill, audit trail — `blocked` in navigation.ts, test-enforced); organization
profile edit; member invite/role change; profile/password edit; email
verification and password reset (sign-up accepts any address unverified;
`/forgot-password` is public in proxy.ts but has no page or API); number
sequences; tax settings; organization default accounts (invoices need an
explicit receivable account, items need their own accounts); seeding of units
and chart of accounts for a new organization; documents-by-entity listing
(no attachments panel); document review GET; folder/tag APIs; a flag in
`ai/ask` saying the provider is fake; automation catalog field/operator
metadata (mirrored in features/automation/contract.ts); preserving a webhook
secret on rule PATCH; stock adjustment reversal endpoint; payment reversal;
list filters by source document (`source_quote`, `source_sales_order`,
account on payments/expenses); cross-entity search; notifications.

## VERSION TRAPS (found by checking, not by assuming)

- `typescript@7` is `latest` but `typescript-eslint` caps at `<6.1.0` — TS 7
  breaks the lint gate entirely. Pinned to 5.9.3.
- `eslint@10` is `latest` but `eslint-plugin-react@7.37.5` (the newest) peers
  only up to `^9.7` and throws at rule-load. Pinned to 9.39.5.
- `vitest@5`, `jsdom@30` and `@testing-library/jest-dom@6.10` all require
  Node ≥22; this project targets Node 20. Pinned to 4.1.11 / 29.0.0 / 6.9.1.
- `eslint-config-next@16` ships native flat config — do NOT use `FlatCompat`.
- React Compiler lint flags react-hook-form's `watch()`; use `useWatch` /
  `Controller`.
- A function exported from a "use client" module is only a client reference
  on the server — keep pure helpers in plain modules.

## TOKEN DISCIPLINE

Search before reading. Open feature-local files only. Do not scan
`node_modules/`, `.next/`, `coverage/`, `playwright-report/`, `test-results/`.
Do not restate the root CLAUDE.md here.

<!-- BEGIN:nextjs-agent-rules -->

# This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` (resolved from this file's directory; in monorepos the `next` package may not be visible from the repo root) before writing any code. Heed deprecation notices.

This block is written and re-added by `next dev` — verify at `node_modules/next/dist/server/lib/generate-agent-files.js`. Removing it from a diff only re-creates the uncommitted change; committing it with your work keeps the tree clean.

<!-- END:nextjs-agent-rules -->
