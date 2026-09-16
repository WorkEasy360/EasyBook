# AUDIT

PURPOSE
Append-only audit trail for financial/security-relevant mutations across every module. Never the source of truth for business state — only a record of what happened.

OWNS
- `AuditLog` (`TenantScopedModel`, RLS-protected, org-scoped) — `.save()` refuses to update an existing row, `.delete()` always raises.
- `services.record(...)` — the only sanctioned way to write an entry.

DOES NOT OWN
- Deciding WHEN to audit — that's each calling module's responsibility (accounting, sales, purchases, banking, ... call `audit.services.record()` after their own mutation commits).

DEPENDENCIES
- `core.models.TenantScopedModel`, `core.tenancy`

INVARIANTS
- Call `audit.services.record()` AFTER the mutation it describes has committed (or within the same DB transaction, never speculatively before) — an audit entry for something that got rolled back is worse than no entry.
- Never call `AuditLog.objects.create()` directly from another app — always go through `audit.services.record()`, which self-scopes the tenant context so it works from requests, Celery tasks, and management commands alike.
- `AuditLog` rows are immutable after creation — do not add an update path, a "soft edit", or a bulk-delete/retention job without an explicit product decision. Phase 7's GST work is now in (`tax`/`compliance`) and deliberately did NOT add a retention policy here: GST record-keeping requirements are about how long an organization must PRESERVE filings and source documents, which argues against deleting audit history, not for a purge job. `compliance.EInvoiceDocument`/`EWayBill` follow the same append-only, never-deleted shape as `AuditLog` for exactly this reason (`compliance/CLAUDE.md`). Retention/archival, if ever needed, remains a distinct future decision.

TESTS
`audit/tests/test_audit_log.py` — append-only enforcement, per-organization isolation.

SECURITY
`AuditLog` is RLS-protected exactly like other `TenantScopedModel` tables — see `core/CLAUDE.md`.

READ FIRST
- `models.py`, `services.py`
