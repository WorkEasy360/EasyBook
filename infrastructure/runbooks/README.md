# Runbooks

Phase 12 section 71. Each one assumes the Terraform stack in `../terraform/`
has actually been applied — none of these have been exercised against a real
deployment yet (no AWS account was available while writing them), so treat
each as a documented, reasoned-through procedure to validate on first real
use, not a proven-in-production script.

Omitted deliberately, not by oversight — nothing exists yet to have an
outage runbook for:
- **Payment provider outage** — no payment provider is integrated (phase 12
  section 23 is still POST-LAUNCH; see root CLAUDE.md rule 5 on not inventing
  an integration).
- **Compliance/GSP provider outage** — same reasoning; see
  `backend/compliance/CLAUDE.md`'s "PROVIDERS" section for exactly why no
  live e-Invoice/e-Way Bill/GSTR filing client exists yet (only the `manual`
  provider, which records what a human filed on the government portal
  directly — there is no automated call to go down).

## Index

- [deploy.md](deploy.md)
- [database-bootstrap.md](database-bootstrap.md)
- [rollback.md](rollback.md)
- [database-migration-failure.md](database-migration-failure.md)
- [database-restore.md](database-restore.md)
- [redis-celery-outage.md](redis-celery-outage.md)
- [ai-provider-outage.md](ai-provider-outage.md)
- [document-storage-outage.md](document-storage-outage.md)
- [secret-rotation.md](secret-rotation.md)
- [security-incident.md](security-incident.md)

## RPO / RTO

Not yet defined — these are business decisions, not something to invent here
(phase 12 section 53 is explicit: "If none exist, identify them as decisions
required"). Whoever owns that decision should set them before this stack's
first real production deploy; `database-restore.md` and `rollback.md` below
give the actual mechanical time each recovery path takes once real numbers
are measured, which is the input that decision needs.
