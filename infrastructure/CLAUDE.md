# INFRASTRUCTURE

PURPOSE
Local dev infrastructure (Docker Compose) now; IaC for AWS (ECS/Fargate, RDS, ElastiCache, S3, KMS, Secrets Manager) later. Do not hand-provision cloud resources outside of IaC once that exists.

OWNS
- `docker-compose.yml` — local Postgres 16 + Redis for backend development.
- `postgres-init/01-app-role.sql` — creates the non-superuser `easybook_app` role Django connects as. This file is why RLS actually works locally; see the "known local port/role setup" note below before touching it.

DOES NOT OWN
- Application code, migrations (backend/), or frontend build config.

KNOWN LOCAL SETUP NOTES
- Host ports are remapped from the Postgres/Redis defaults (5442 for Postgres, 6390 for Redis) because other local projects on this machine already bind 5432/6379. This is a local-machine accommodation, not a production port choice — production (RDS/ElastiCache) uses standard ports behind security groups.
- The role created via `POSTGRES_USER` in the official Postgres image is ALWAYS a superuser, and superusers unconditionally bypass Row Level Security — `FORCE ROW LEVEL SECURITY` does not override this. `01-app-role.sql` creates a separate `easybook_app` role (not a superuser) that Django actually connects as. In production, the RDS master user must likewise never be the application's runtime DB user.
- The init script only runs against an empty Postgres data volume. If you change it, `docker compose down -v` (destroys the volume) before `docker compose up -d` to have it re-apply.

RULES
- IaC (when introduced): no secrets committed to the repo — use Secrets Manager / environment injection.
- Least privilege for every IAM role and DB role.
- Private subnets for RDS/ElastiCache; no public database endpoints.
- TLS everywhere; encryption at rest via KMS.
- Every infra change needs a rollback path documented in the PR, not just a forward path.

READ FIRST
- `docker-compose.yml`, `postgres-init/01-app-role.sql`
