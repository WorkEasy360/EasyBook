# Database bootstrap and migrations

Not yet exercised against a real AWS environment. The bootstrap script itself
is tested against a real PostgreSQL server
(`backend/ops/tests/test_db_bootstrap.py`), and the task wiring by
`infrastructure/terraform/tests/db_bootstrap.tftest.hcl`.

Two one-off ECS task definitions exist for this (`terraform/oneoff_tasks.tf`).
Neither is a service, and neither publishes a port.

| Task | When | Runs as | Can read the RDS master secret |
|------|------|---------|--------------------------------|
| `db-bootstrap` | Once per environment, before the first migration. Safe to re-run. | RDS master user | Yes, through its own execution role only |
| `migrate` | Before every release's service deployment | `easybook_app` | No |

No service, and not the `migrate` task, can read the master secret. Every
API, worker and beat process refuses to start if its database role is a
superuser or has BYPASSRLS (`backend/core/db_preflight.py`).

Common values, from `terraform output`:

```sh
CLUSTER=$(terraform output -raw ecs_cluster_name)
NETWORK=$(terraform output -json oneoff_task_network)
```

`run-task` needs `--network-configuration "awsvpcConfiguration=$NETWORK"`,
with the JSON passed as the `awsvpcConfiguration` value.

## FIRST ENVIRONMENT BOOTSTRAP

Preconditions:

- `terraform apply` has completed. RDS is available, and the
  `app-db-credentials` secret exists. Terraform generated its password.
- The backend image for the release is in ECR, and `container_image` points
  at it by digest or commit tag.

Steps:

1. Run the bootstrap task and wait for it to stop:

   ```sh
   aws ecs run-task --cluster "$CLUSTER" --launch-type FARGATE \
     --task-definition "$(terraform output -raw db_bootstrap_task_definition_arn)" \
     --network-configuration "awsvpcConfiguration=$NETWORK"
   aws ecs wait tasks-stopped --cluster "$CLUSTER" --tasks <task-arn>
   aws ecs describe-tasks --cluster "$CLUSTER" --tasks <task-arn> \
     --query 'tasks[0].containers[0].exitCode'
   ```

2. Exit code `0` means the task succeeded. Its log line
   (`/ecs/<name>/db-bootstrap`) reads `db_bootstrap_succeeded`, and lists
   `app_role`, `pgvector_version` and the verified attributes. No password is
   ever logged.
3. The task did the following, idempotently:
   - It created the `vector` extension.
   - It created or re-asserted `easybook_app` with LOGIN, NOCREATEDB and
     NOCREATEROLE, using the secret's password.
   - It granted CONNECT on the database, and USAGE and CREATE on schema
     `public`.
   - It verified the role has no SUPERUSER, BYPASSRLS, REPLICATION or
     CREATEROLE, and is not a member of `rds_superuser`.
4. Run the first migration. See **NORMAL MIGRATION** below.
5. Deploy the services (`deploy.md` step 2 onward).

## NORMAL MIGRATION

Every release, before the services roll:

```sh
aws ecs run-task --cluster "$CLUSTER" --launch-type FARGATE \
  --task-definition "$(terraform output -raw migrate_task_definition_arn)" \
  --network-configuration "awsvpcConfiguration=$NETWORK"
```

The task runs `manage.py db_preflight && manage.py migrate --noinput` as
`easybook_app`, with `DB_STATEMENT_TIMEOUT_MS=0`. The preflight fails the
task before any DDL if the role is unsafe. Wait for exit code `0`, then roll
the services.

Before running a migration, review what it will do. Reject unexpected
`DROP TABLE`, `DROP COLUMN` or destructive data migrations. Use
expand/contract across releases instead (`database-migration-failure.md`).

## ROLLBACK / FAILURE

- **Bootstrap exit 1, with `db_bootstrap_failed` and `Missing required
  environment variables`.** The task definition's secrets did not resolve.
  Check that the secret ARNs exist and that the `db-bootstrap-execution`
  role can read them.
- **Bootstrap exit 1, with `db_bootstrap_failed` naming `sqlstate`.**
  - `28P01` means authentication failed. The master secret may have been
    rotated during the run, so run the task again.
  - `08001` or `08006` means connectivity failed. Check the task security
    group, the RDS security group and the private subnets.
  - `42501` means insufficient privilege. The task did not run as the master
    user.
- **Bootstrap exit 1, with `Application role ... is unsafe`.** An existing
  `easybook_app` has a dangerous attribute or membership. The transaction
  was rolled back, so nothing changed. As the master user, remove the
  attribute, for example `ALTER ROLE easybook_app NOBYPASSRLS` (on RDS this
  may need AWS Support if it is a true superuser). Then re-run. **Do not**
  deploy services against that role: they will refuse to start anyway.
- **The bootstrap is always safe to re-run.** Re-running it is also how an
  app-password rotation is applied (`secret-rotation.md`). Update the secret,
  re-run `db-bootstrap`, then force a new deployment of the services.
- **Migrate exit 1 from `db_preflight`.** The role is unsafe, as above.
  Nothing was migrated.
- **Migrate exit 1 from a migration.** Follow
  `database-migration-failure.md`. Django rolls back each failed
  migration's transaction. Do not deploy the new services. The previous
  release keeps running against the unchanged schema.
- **Rolling back a release** (`rollback.md`) does not reverse migrations.
  Expand/contract keeps the previous release compatible with the migrated
  schema. A migration that cannot be kept compatible must not ship in the
  same release as the code that needs it.
