# EasyBook AWS infrastructure (Terraform)

Phase 12 slice 3/5 IaC foundation. Written and syntax-validated
(`terraform validate`, `terraform fmt`) locally — **never applied against a
real AWS account** (no credentials were available in the session that wrote
this, and doing so requires explicit human approval regardless — see root
`CLAUDE.md` and the phase 12 prompt's "IMPORTANT PRODUCTION ACTION RULE").
Review every default in `variables.tf` before the first real `apply`.

## What this does NOT do

- **Does not push an image.** `.github/workflows/backend-ci.yml`'s `container`
  job builds and scans an image but does not yet push to `aws_ecr_repository.backend`
  — add a push step once this stack is actually applied and its ECR repo exists.
- **Does not create the app's own Postgres role.** RDS's master user
  (`manage_master_user_password`, AWS-managed) is never the app's runtime
  role (global rule 4). Terraform has no network path into the private-subnet
  RDS instance from wherever `apply` runs, so this is a one-time manual/CI
  step — see below.
- **Does not run `CREATE EXTENSION vector;`.** Same reason. `backend/ai/checks.py`'s
  `pre_migrate` hook fails the `ai` app's migration clearly if this is skipped
  — it will not fail silently.
- **Does not stand up CloudFront.** See `waf.tf`'s header comment: WAF is
  attached directly to the ALB (regional scope) instead, since there is no
  frontend yet to justify a CDN hop. Revisit once `frontend/` has a real build.
- **Does not configure a custom domain/TLS cert** unless `var.domain_name` /
  `var.acm_certificate_arn` are set — issuing and validating an ACM
  certificate is a manual (or separate Terraform run's) step tied to whichever
  DNS provider actually holds the domain.

## One-time post-`apply` setup

Run these once, from something with network access to the VPC (a bastion, an
ECS Exec session into a running task, or a one-off ECS `RunTask` — not from a
laptop, since RDS/Redis are private-subnet only by design):

```sql
-- 1. As the RDS master user (password: the secret at
--    `terraform output rds_master_user_secret_arn`):
CREATE EXTENSION IF NOT EXISTS vector;

-- 2. Create the app's own non-superuser role (password: the secret at
--    `terraform output app_db_credentials_secret_arn`, key "password").
--    Mirrors infrastructure/postgres-init/01-app-role.sql exactly.
CREATE ROLE easybook_app WITH LOGIN PASSWORD '<from-the-secret>';
GRANT ALL PRIVILEGES ON DATABASE easybook TO easybook_app;
GRANT CREATE, USAGE ON SCHEMA public TO easybook_app;
```

Then run migrations once (also needs VPC network access — a one-off ECS
`RunTask` using the `api` task definition with `command` overridden to
`["python", "manage.py", "migrate"]` and `DB_STATEMENT_TIMEOUT_MS=0` in its
environment overrides is the standard pattern (every app connection otherwise
cancels statements after 30s, which a large index build can exceed); do not bake an
automatic `migrate` into every container's normal startup — phase 12 section
48-49 on expand/migrate/contract deploys and avoiding concurrent migration races).

## Remote state (required)

State lives in S3 with locking (`versions.tf`). Local state is not an option:
two people — or a person and CI — applying at once overwrite each other, and
the only record of what Terraform manages would sit on one laptop.

Locking uses S3-native lock files (`use_lockfile`, Terraform >= 1.11), so there
is no DynamoDB table to create or pay for; the DynamoDB lock arguments are
deprecated.

The state bucket cannot be created by the run whose state it holds, so
`bootstrap/` creates it once per account, with local state of its own:

```
cd bootstrap
terraform init
terraform apply -var state_bucket_name=easybook-terraform-state-<account-id>
```

It creates a versioned, KMS-encrypted, public-access-blocked, TLS-only bucket
with `prevent_destroy` set.

Then initialise this stack against it, once per environment (the backend is a
partial configuration: nothing environment-specific is hard-coded):

```
cp backend/example.s3.tfbackend backend/staging.s3.tfbackend   # fill in
terraform init -backend-config=backend/staging.s3.tfbackend
```

`terraform init` with no `-backend-config` fails ("The attribute \"bucket\" is
required by the backend") rather than silently falling back to local state.
Each environment gets its own state key; never share one.

## Variables that have no safe default

`terraform apply` will prompt for (or fail without) these — see each one's
description in `variables.tf` for why a default would be wrong to invent:

- `environment` — `staging` or `production`; never share one `.tfstate`/database/secret set between them.
- `container_image` — CI/CD supplies this per build; must never float on `:latest`.

Everything else has a documented default suitable for a first `staging` apply,
including `domain_name = ""` and `acm_certificate_arn = ""` — the stack is
apply-able and smoke-testable over plain HTTP against the ALB's own DNS name
before either exists, but `config/settings/production.py`'s own preflight
(`config/settings/preflight.py`) will correctly refuse to boot the Django app
with `DJANGO_ALLOWED_HOSTS` empty, so the ECS `api` service will crash-loop
until `domain_name` is set — that is the intended fail-closed behavior, not a
bug to work around here.

## Validating changes

```
terraform fmt -check -recursive
terraform init            # downloads providers only; no AWS credentials needed
terraform validate
```

`terraform plan`/`apply` need real AWS credentials and are out of scope for
this repo's automated checks.
