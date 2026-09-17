# Database backup / restore

**Status: procedure documented, not yet exercised against a real RDS
instance** (no AWS account was available while writing this). Phase 12
section 52 is explicit that a backup without a tested restore is not
considered complete — treat this stack's backup posture as unproven until
someone actually runs the steps below once and records how long it took.

## What's already configured (terraform/rds.tf, terraform/s3.tf)

- RDS automated backups, `var.db_backup_retention_days` (default 7), which
  is what gives point-in-time recovery within that window.
- `storage_encrypted = true` — a restored snapshot stays encrypted.
- S3 document bucket versioning — a deleted/overwritten object is recoverable
  independent of any database restore.

## Restore procedure (run this once against staging to actually prove it works)

1. **Snapshot or PITR target.** For a specific bad event, restore-to-point-in-time
   to a few minutes before it (`aws rds restore-db-instance-to-point-in-time`);
   for validating the backup posture in general, restore the latest automated
   snapshot. Either way, restore into a **new** RDS instance — never in place
   over the running one.
2. **Isolated environment.** The restored instance must sit in the same VPC
   pattern as `terraform/vpc.tf` (private subnets, `terraform/security_groups.tf`'s
   RDS security group) but reachable only from a throwaway/staging ECS task
   set, never from production traffic, until verified.
3. **`CREATE EXTENSION vector;`** again, as the master user — a restored
   snapshot includes the extension's installed state already (it's part of
   the database), so this step should be a no-op confirming it, not a fresh
   install. If it's missing, that itself is a finding: the extension was
   somehow not present at snapshot time.
4. **Application can connect.** Point a throwaway `api` task (env vars
   overridden to the restored instance's endpoint) at it and hit
   `/api/v1/health/` — expects `{"database": true}`.
5. **RLS still enabled.** Run `backend/core/tests/test_tenant_isolation.py`
   against the restored instance (point `DB_HOST`/`DB_PORT` at it via
   `config.settings.test` overrides) — this is the actual acceptance test for
   "RLS survived the restore," not a manual spot-check.
6. **Integrity checks.** Run the full backend test suite's non-destructive
   read paths, or at minimum: pick a handful of real organizations from the
   restored data and confirm `accounting.services.reports`-derived Trial
   Balance debits equal credits (root CLAUDE.md global rule 2) — a restore
   that silently lost or duplicated journal lines would show up here first.
7. **Document references.** Confirm a sample of `documents.Document` rows'
   S3 keys still resolve (the S3 bucket is a separate resource from RDS —
   a database restore does not, on its own, restore or affect S3 objects;
   this step is really "prove the two were never allowed to drift apart,"
   not "restore S3").
8. **Tear down** the restored instance once verified — it is a real,
   billed RDS instance for as long as it exists.

## Recording the result

Once run for real: record wall-clock time for steps 1-4 (this is the actual
RTO input phase 12 section 53 asks for) and note here whether anything in
this procedure was wrong or missing.
