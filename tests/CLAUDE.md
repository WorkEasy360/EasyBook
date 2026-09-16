# TESTS

PURPOSE
Cross-cutting/E2E tests that span backend + frontend, or exercise the deployed system end-to-end. Module-local unit/integration tests live next to the code they test (`backend/<app>/tests/`), not here.

STATUS
Empty — no E2E suite yet (no frontend to drive end-to-end). Backend acceptance tests (tenant isolation, auth, accounting invariants as they're built) live in each app's `tests/` package; see `backend/CLAUDE.md`.

WHEN THIS DIRECTORY GETS USED
- Playwright/Cypress-style E2E flows once the frontend exists.
- Cross-service contract tests if/when integrations are added (Phase 12+).

TESTS REQUIRED (once populated)
- Golden-path E2E per major workflow (quote→invoice→payment→reconciliation, bill→payment, etc.) once those modules exist.
