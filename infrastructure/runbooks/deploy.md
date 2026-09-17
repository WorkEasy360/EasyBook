# Deploy

## Preconditions

- `backend-ci.yml`'s `static`, `test`, and `container` jobs are green for the
  commit being deployed (ruff, bandit, pip-audit, migration drift, full test
  suite, Trivy scan — `.github/workflows/backend-ci.yml`).
- The image built by that `container` job is pushed to
  `terraform output ecr_repository_url`, tagged with the commit SHA — never
  `:latest` (`infrastructure/terraform/variables.tf`'s `container_image`
  description explains why).

## Steps

1. **Migrate first, deploy second** (expand/contract — phase 12 section
   48-49). Run migrations as a one-off ECS task using the `api` task
   definition with `command` overridden to `["python", "manage.py", "migrate"]`
   — never as part of every container's normal startup (concurrent replicas
   would race). Watch for `ai/checks.py`'s pgvector preflight error if this
   is a fresh environment (`infrastructure/terraform/README.md`'s one-time
   setup section).
2. Update `container_image` to the new tag and `terraform apply` (or, once
   CI/CD pushes directly, trigger an ECS service `update-service
   --force-new-deployment` with the new task definition revision — either
   way, one commit's image, deployed to every service in
   `var.ecs_services` that shares it).
3. ECS performs a rolling deployment per service (`ecs.tf`'s
   `deployment_maximum_percent`/`deployment_minimum_healthy_percent` —
   note `beat` deploys via kill-then-start, not rolling, deliberately: see
   its comment in `ecs.tf`).
4. Watch the `api` service's ECS deployment circuit breaker and the ALB
   target group's healthy-host count (`/api/v1/health/` — readiness, not
   liveness) before considering the deploy done.
5. Post-deploy smoke test: hit `/api/v1/health/live/` and `/api/v1/health/`
   directly, and one real authenticated read (e.g. list organizations).

## If it goes wrong mid-deploy

See [rollback.md](rollback.md). Do not manually edit a running task's
environment or exec into it to "fix" something — roll back to the last known
good image/task-definition revision instead.
