# Rollback

## Application code (no schema change involved)

ECS keeps prior task definition revisions. Fastest path:

```
aws ecs update-service --cluster <cluster> --service <service> \
  --task-definition <family>:<previous-revision>
```

Do this per service in `var.ecs_services` that actually changed — not
necessarily all six. Confirm via the ALB target group's health and a smoke
test (same checks as `deploy.md` step 5) before declaring it resolved.

## A migration shipped with the bad code

This is why `deploy.md` insists on expand/contract (phase 12 section 48-49):
a migration that only *adds* (a nullable column, a new table) is safe to
leave in place while the application code rolls back — the old code simply
never reads the new column. A migration that *removes or renames* something
the previous code version still expects cannot be rolled back by reverting
the app alone; you must also reverse the migration
(`python manage.py migrate <app> <previous_migration_name>`) as a one-off ECS
task, in the same order deploy.md uses for forward migrations, BEFORE rolling
the application code back — otherwise the old code hits a missing
column/table. This is precisely why a destructive migration should never
ship in the same deploy as the code that depends on it.

## Database (RDS)

Only for actual data corruption, not a bad deploy — see
[database-restore.md](database-restore.md). A schema-only rollback (previous
paragraph) does not need a database restore.

## Rollback does NOT mean

- `terraform destroy` or `apply`-ing an old Terraform commit for
  infrastructure that hasn't actually changed — that risks recreating
  RDS/ElastiCache/S3 resources with new identifiers, losing data. Only roll
  back the ECS task definition / image tag unless the infrastructure itself
  (not just the app) is the thing that broke.
