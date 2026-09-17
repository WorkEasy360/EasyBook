# Database migration failure

## Symptom

The one-off `manage.py migrate` ECS task (`deploy.md` step 1) exits non-zero,
or `manage.py makemigrations --check --dry-run` would have caught this in CI
but the failure only surfaces against real production data shape/volume.

## First: is it the pgvector preflight?

`backend/ai/checks.py`'s `pre_migrate` hook raises `ImproperlyConfigured`
with a message naming `CREATE EXTENSION vector` if the `ai` app's migration
is about to run against a database missing the pgvector extension. This is
not a migration bug — it's the preflight doing its job. Fix: run
`CREATE EXTENSION IF NOT EXISTS vector;` as the RDS master user
(`infrastructure/terraform/README.md`'s one-time setup), then re-run migrate.

## Otherwise

1. **Do not retry blindly.** Read the actual Postgres error — this codebase
   already has RLS enabled on every tenant-owned table
   (`backend/core/rls.py`), and a migration that adds a constraint or
   `NOT NULL` column against a live table can fail on real, pre-existing data
   that a dev/test fixture never exercised.
2. Django wraps each migration in a transaction by default — a failed
   migration should have rolled back cleanly, leaving the schema at its
   previous, working state. Confirm this before doing anything else:
   `python manage.py showmigrations <app>` from a one-off task with DB
   access, checking the failed migration shows as NOT applied.
3. Fix the migration (or its data-backfill step) in code, going through the
   full CI gate again — never hand-edit a migration file already applied to
   any other environment, and never run `manage.py migrate --fake` to paper
   over a genuine failure.
4. For a migration that's inherently risky at real scale (locking a large
   table, an expensive backfill) — see phase 12 section 48: expand
   (nullable/additive) → deploy compatible code → backfill in batches → contract
   (drop the old column/constraint) as separate deploys, not one migration.

## If a migration partially applied data corruption before failing

This is a data-integrity incident, not just a migration bug — escalate per
[security-incident.md](security-incident.md)'s severity triage even though
it isn't a security issue; the response shape (assess blast radius, decide
whether a restore is warranted, communicate) is the same. See
[database-restore.md](database-restore.md) if a restore is warranted.
