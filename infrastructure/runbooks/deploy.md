# Deploy

## Preconditions

- The commit is on `main` (merged through a pull request). Production and
  staging deploy only exact `origin/main` SHAs, never a feature branch.
- Every workflow is green for that SHA: `backend-ci`, `frontend-ci`,
  `infrastructure-ci` and `security`. Between them they cover lint, types,
  tests, migration drift, Terraform tests, dependency audits, the secret scan,
  image builds and container smoke tests, and Trivy.
- Both images exist in ECR tagged with that SHA. The `publish` jobs push
  them after the gates pass. Check with
  `aws ecr describe-images --repository-name <repo> --image-ids imageTag=<sha>`
  for the backend and the frontend repository. Never deploy `:latest`. The
  repositories are IMMUTABLE, so a SHA tag always means the same image.
- Record the SHA currently running, so there is a known-good image to roll
  back to (`rollback.md`).

## Steps

1. **Migrate first, deploy second** (expand/contract, phase 12 sections
   48-49). Run the one-off `migrate` task
   (`terraform output migrate_task_definition_arn`), never a migration inside
   every container's normal startup, where concurrent replicas would race.
   The exact command and failure handling are in
   [database-bootstrap.md](database-bootstrap.md). A brand-new environment
   runs the `db-bootstrap` task once before its first migration.
2. Update `container_image` and `frontend_container_image` to the new SHA tag and `terraform apply` (or, once
   CI/CD pushes directly, trigger an ECS service `update-service
   --force-new-deployment` with the new task definition revision — either
   way, one commit's image, deployed to every service in
   `var.ecs_services` that shares it).
3. ECS performs a rolling deployment per service (`ecs.tf`'s
   `deployment_maximum_percent`/`deployment_minimum_healthy_percent` —
   note `beat` deploys via kill-then-start, not rolling, deliberately: see
   its comment in `ecs.tf`).
4. Watch the `api` service's ECS deployment circuit breaker and the ALB
   target group's healthy-host count before considering the deploy done.
   The target group probes liveness (`/api/v1/health/live/`). Check readiness
   (`/api/v1/health/`) separately, as in step 5.
5. Post-deploy smoke test: hit `/api/v1/health/live/` and `/api/v1/health/`
   directly, and one real authenticated read (e.g. list organizations).

## If it goes wrong mid-deploy

See [rollback.md](rollback.md). Do not manually edit a running task's
environment or exec into it to "fix" something — roll back to the last known
good image/task-definition revision instead.
