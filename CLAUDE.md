PROJECT
Industrial-grade multi-tenant accounting SaaS (original product; Zoho Books/Xero/QuickBooks/Tally/FreshBooks/Odoo are competitive references only — never copy code, UI, or branding).

STACK
Django + DRF (ASGI) / PostgreSQL 16+ with pgvector and RLS / Celery + Redis / Next.js + TypeScript / S3-compatible storage.

ARCHITECTURE
Modular monolith, API-first, strict module boundaries. No microservices until justified.

Accounting pipeline (never invert this):
Business Data -> Deterministic Accounting Engine -> Ledger/Journals/Reports/Tax -> Structured APIs/Tools -> AI + RAG -> User Answers.
AI may explain, retrieve, summarize, suggest, extract, draft. AI must NEVER independently determine authoritative balances, journal totals, GST liability, tax, inventory valuation, financial statements, or reconciliation status.

GLOBAL RULES

1. Security and tenant isolation are mandatory (app-level scoping + PostgreSQL RLS, fail closed).
2. Accounting uses deterministic double-entry logic. debits == credits on every posted journal, always.
3. Monetary calculations use Decimal. Never float for money.
4. Never bypass organization scoping/RLS.
5. Do not invent APIs, libraries, or compliance rules.
6. Verify version-sensitive information (framework versions, GST/e-Invoice/e-Way Bill rules, payment gateway APIs, cloud infra) against current official documentation before implementing.
7. Reuse existing abstractions before creating new ones.
8. Add dependencies only when justified; pin production dependencies.
9. Prefer small, targeted changes over broad rewrites.
10. Tests are required for behavioral changes, especially accounting invariants and tenant isolation.
11. Never expose secrets. No secrets in the repository.
12. Preserve auditability for every financial mutation (append audit records; never silently edit posted data).

TOKEN / CONTEXT RULES

1. Do not read the entire repository automatically.
2. Start with: root CLAUDE.md, current directory's CLAUDE.md, directly relevant files.
3. Search filenames/symbols first; open only relevant sections of large files.
4. Do not repeatedly reread unchanged files. Use git diff/status for recent changes.
5. Do not print entire files when a focused patch is sufficient.
6. Do not generate long explanations unless requested; do not repeat requirements already in CLAUDE.md.
7. Prefer one focused implementation task at a time.
8. Do not launch agents/subagents unless they materially improve the task.
9. Do not perform broad web research for ordinary coding questions.

VERIFICATION RULE

If knowledge may be stale and affects security, Django/Next/PostgreSQL versions, third-party API behavior, GST/e-Invoice/e-Way Bill requirements, payment gateway APIs, cloud infrastructure, or dependency compatibility: verify against current official documentation before implementing. Source priority: official docs > official specs > official repo/release notes > other trusted primary sources.

Workflow: UNDERSTAND -> SEARCH -> VERIFY -> PLAN -> IMPLEMENT -> TEST.

REPO MAP
- backend/ — Django project (see backend/CLAUDE.md)
- frontend/ — Next.js app (see frontend/CLAUDE.md)
- infrastructure/ — Docker/IaC (see infrastructure/CLAUDE.md)
- docs/ — architecture and product docs
- tests/ — cross-cutting/E2E tests (see tests/CLAUDE.md)

DO NOT scan node_modules/, .next/, venv/, .venv/, dist/, build/, coverage/, staticfiles/, or migration files unless explicitly necessary.
