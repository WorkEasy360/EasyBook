# INFRASTRUCTURE

PURPOSE
Local dev infrastructure (Docker Compose) and, as of phase 12 slice 3/5, the Terraform IaC foundation for AWS (VPC, ECS/Fargate, RDS, ElastiCache, S3, WAF, Secrets Manager — see `terraform/README.md`). Never hand-provision cloud resources outside of IaC.

OWNS
- `terraform/` — AWS IaC foundation (phase 12 slice 3/5). Written and
  `terraform validate`/`fmt`-checked locally; never applied against a real
  AWS account from this repo without explicit human approval (root
  CLAUDE.md's production action rule). See `terraform/README.md` for what it
  deliberately does not do yet (push images, create the app's own DB role,
  run `CREATE EXTENSION vector`, CloudFront) and why.
- `docker-compose.yml` — local Postgres 16 + Redis for backend development.
- `postgres-init/01-app-role.sql` — creates the non-superuser `easybook_app` role Django connects as. This file is why RLS actually works locally; see the "known local port/role setup" note below before touching it.
- `postgres-init/02-pgvector.sql` — installs the pgvector extension (Phase 10 AI/RAG) into `easybook` and `template1` as the superuser; the app role cannot (pgvector is not a trusted extension). The Postgres image is `pgvector/pgvector:pg16-trixie`: the official postgres:16 image on the same Debian trixie base plus pgvector, so an existing postgres:16 data volume is reused without a dump/restore or collation change. For an EXISTING volume, run both statements once by hand: `docker exec infrastructure-postgres-1 psql -U easybook -d easybook -c "CREATE EXTENSION IF NOT EXISTS vector;"` and the same with `-d template1`. Rollback: revert the image to `postgres:16` only after `manage.py migrate ai zero` (tables using the `vector` type must be gone first). Production (RDS PostgreSQL 16) supports pgvector; `rds_superuser` must create the extension before deploy.

DOES NOT OWN
- Application code, migrations (backend/), or frontend build config.
- `backend/Dockerfile` (production container image, one image for every ECS
  service — see its own header comment and `backend/CLAUDE.md`), `backend/.dockerignore`,
  `backend/.trivyignore` — these live beside the code they build/scan rather
  than here, but are deployment-relevant: keep them in sync with this file's
  invariants (non-root, no secrets baked in, pinned base image) when either changes.

KNOWN LOCAL SETUP NOTES
- Host ports are remapped from the Postgres/Redis defaults (5442 for Postgres, 6390 for Redis) because other local projects on this machine already bind 5432/6379. This is a local-machine accommodation, not a production port choice — production (RDS/ElastiCache) uses standard ports behind security groups.
- The role created via `POSTGRES_USER` in the official Postgres image is ALWAYS a superuser, and superusers unconditionally bypass Row Level Security — `FORCE ROW LEVEL SECURITY` does not override this. `01-app-role.sql` creates a separate `easybook_app` role (not a superuser) that Django actually connects as. In production, the RDS master user must likewise never be the application's runtime DB user.
- The init script only runs against an empty Postgres data volume. If you change it, `docker compose down -v` (destroys the volume) before `docker compose up -d` to have it re-apply.

RULES
- No secrets committed to the repo — `terraform/secrets.tf` creates Secrets Manager containers (some Terraform-generated, e.g. the app DB password; some deliberately left empty for out-of-band population — see its header comment), never a real vendor credential in code or state.
- Least privilege for every IAM role and DB role.
- Private subnets for RDS/ElastiCache; no public database endpoints.
- TLS everywhere; encryption at rest via KMS.
- Every infra change needs a rollback path documented in the PR, not just a forward path.

READ FIRST
- `docker-compose.yml`, `postgres-init/01-app-role.sql`
- `terraform/README.md` for anything AWS/deployment-related
