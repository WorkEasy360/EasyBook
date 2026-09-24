# EasyBook AWS infrastructure (Terraform)

Phase 12 slice 3/5 IaC foundation. Written and syntax-validated
(`terraform validate`, `terraform fmt`) locally — **never applied against a
real AWS account** (no credentials were available in the session that wrote
this, and doing so requires explicit human approval regardless — see root
`CLAUDE.md` and the phase 12 prompt's "IMPORTANT PRODUCTION ACTION RULE").
Review every default in `variables.tf` before the first real `apply`.

## Before the first `apply`: what you must supply

HTTPS is mandatory. There is no plaintext mode, and no way to serve the app
on the ALB's own `*.elb.amazonaws.com` name, because nobody can obtain a
certificate for it. You need:

1. **A hostname you control DNS for**, passed as `domain_name`. It must be a
   bare lowercase hostname such as `staging.books.example.com`, with no
   scheme, port, path or wildcard. Staging and production each use their own
   hostname. Every origin setting derives from it: Django's `ALLOWED_HOSTS`,
   `CSRF_TRUSTED_ORIGINS` and `CORS_ALLOWED_ORIGINS`, and the frontend's
   `API_BASE_URL`. Both applications refuse to start if any of these is
   missing or unsafe.
2. **An issued ACM certificate** for exactly that hostname, in this stack's
   region, passed as `acm_certificate_arn`. Validate it through DNS at your
   DNS provider before running `apply`.
3. **The two image URIs** for the release, `container_image` and
   `frontend_container_image`. Both use the same commit SHA and are never
   `:latest`. CI pushes them after merge to `main`
   (`.github/workflows/release-images.yml`), once the deploy role exists.

After `apply`, point the hostname at the ALB with a CNAME or ALIAS record to
`terraform output alb_dns_name`. The app is then served at
`terraform output app_url`.

## What this does NOT do

- **Does not create DNS records or certificates.** Both belong to whichever
  DNS provider holds the domain. See above.
- **Does not bootstrap the database during `apply`.** Terraform has no
  network path into the private-subnet RDS instance. The one-off
  `db-bootstrap` and `migrate` ECS tasks do it instead (see below).
- **Does not stand up CloudFront.** WAF is attached directly to the ALB
  (regional scope). See `waf.tf`.
- **Does not expose Django admin.** Only `/api/v1/*` is routed to the API;
  everything else goes to the frontend.

## One-time post-`apply` setup

Database bootstrap (pgvector and the non-superuser `easybook_app` role) and
every migration run as one-off ECS tasks defined in `oneoff_tasks.tf`. There
is no bastion, no ECS Exec, and no psql in any image. Only the `db-bootstrap`
task can read the RDS master secret. Exact commands, and what to do when
either task fails, are in
[`../runbooks/database-bootstrap.md`](../runbooks/database-bootstrap.md).

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

## Frontend wiring

The `web` service (`ecs.tf`) reads `<name>/bff-proxy-secret` as
`BFF_PROXY_SECRET`, the same secret the API uses to trust the browser
address the BFF forwards (`backend/core/client_ip.py`). `TRUSTED_PROXY_COUNT`
is 1 on both, because the ALB is the only proxy. The frontend reaches the
API through `https://<domain_name>/api/v1`: the same ALB, TLS and WAF path as
any client. Its NAT egress addresses are exempt from the WAF's per-IP rate
rule, because Django throttles per real client instead (`waf.tf`).

## Variables that have no safe default

`terraform apply` fails without these. See each one's description in
`variables.tf`.

- `environment`: `staging` or `production`. Never share state, databases or
  secrets between them.
- `domain_name` and `acm_certificate_arn`: see "Before the first `apply`".
- `container_image` and `frontend_container_image`: the release's immutable
  image URIs.

## Validating changes

```
terraform fmt -check -recursive
terraform init -backend=false   # providers only; no AWS credentials needed
terraform validate
terraform test                  # mocked providers; no AWS credentials, no AWS calls
```

`terraform plan`/`apply` against a real account need real credentials and
explicit approval. They are out of scope for this repo's automated checks.
