# FRONTEND

STATUS
Not yet scaffolded. Phase 0 delivered the backend foundation (auth, orgs, tenant isolation, RLS) first per the project's build order — the frontend starts once there's a stable API to build against. When scaffolding begins: Next.js (App Router) + React + TypeScript, targeting Node 20+.

PURPOSE (once built)
Professional SaaS accounting UI consuming the `backend/` REST API under `/api/v1/`. Original design — never a pixel-for-pixel clone of Zoho Books/Xero/QuickBooks/etc.

RULES (apply from the first commit)
- Reuse a single design system/component library — no duplicated one-off components for the same UI pattern.
- Typed API contracts — generate or hand-write TypeScript types matching the DRF serializers; do not use `any` for API response shapes.
- Accessible components (keyboard navigation, ARIA where needed) — this is an accounting tool people use for hours at a time.
- Avoid unnecessary client-side state; prefer server data + minimal local UI state.
- Respect a performance budget: route/code splitting, pagination/virtualization for large tables (invoice lists, GL, etc. will get large).
- Auth: the backend issues JWT access/refresh pairs (`/api/v1/auth/login/`, `/api/v1/auth/refresh/`); every org-scoped request must include `X-Organization-Id` for the selected organization (see `backend/core/CLAUDE.md` — requests without it are rejected, fail closed by design).

DO NOT READ BY DEFAULT (once scaffolded)
- `node_modules/`, `.next/`, `dist/`, `build/`, `coverage/`
