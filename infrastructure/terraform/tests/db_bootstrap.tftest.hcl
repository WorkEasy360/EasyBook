# Database bootstrap path (P0 remediation): the first environment could not
# be brought up — no executable path created pgvector or the application role
# — and every service's execution role could read the RDS master secret that
# no container referenced.

mock_provider "aws" {
  override_during = plan
  source          = "./tests/mocks"
}

mock_provider "random" {}

variables {
  environment     = "staging"
  container_image = "123456789012.dkr.ecr.ap-south-1.amazonaws.com/easybook-staging-backend@sha256:0000000000000000000000000000000000000000000000000000000000000000"

  frontend_container_image = "123456789012.dkr.ecr.ap-south-1.amazonaws.com/easybook-staging-frontend@sha256:0000000000000000000000000000000000000000000000000000000000000000"
  domain_name              = "staging.books.example.com"
  acm_certificate_arn      = "arn:aws:acm:ap-south-1:123456789012:certificate/00000000-0000-0000-0000-000000000000"
}

run "services_cannot_read_the_rds_master_secret" {
  command = plan

  assert {
    condition = !contains(
      local.service_execution_secret_arns,
      "arn:aws:secretsmanager:ap-south-1:123456789012:secret:rds-master",
    )
    error_message = "The shared ECS execution role must not be able to read the RDS master secret."
  }
}

run "only_the_bootstrap_task_reads_the_master_secret" {
  command = plan

  assert {
    condition = contains(
      local.db_bootstrap_execution_secret_arns,
      "arn:aws:secretsmanager:ap-south-1:123456789012:secret:rds-master",
    )
    error_message = "The db-bootstrap execution role must be able to read the RDS master secret."
  }

  assert {
    condition     = length(local.db_bootstrap_execution_secret_arns) == 2
    error_message = "The db-bootstrap execution role reads exactly the master secret and the app DB credentials."
  }

  override_resource {
    target          = aws_iam_role.db_bootstrap_execution
    override_during = plan
    values          = { arn = "arn:aws:iam::123456789012:role/db-bootstrap-execution" }
  }

  override_resource {
    target          = aws_iam_role.ecs_execution
    override_during = plan
    values          = { arn = "arn:aws:iam::123456789012:role/ecs-execution" }
  }

  assert {
    condition     = aws_ecs_task_definition.db_bootstrap.execution_role_arn == "arn:aws:iam::123456789012:role/db-bootstrap-execution"
    error_message = "db-bootstrap must use its own execution role."
  }

  assert {
    condition = alltrue(concat(
      [aws_ecs_task_definition.migrate.execution_role_arn == "arn:aws:iam::123456789012:role/ecs-execution"],
      [for task in aws_ecs_task_definition.service : task.execution_role_arn == "arn:aws:iam::123456789012:role/ecs-execution"],
    ))
    error_message = "Services and the migrate task use the shared execution role, which cannot read the master secret."
  }
}

run "bootstrap_and_migrate_are_one_off_tasks" {
  command = plan

  assert {
    condition     = local.db_bootstrap_command == ["python", "-m", "ops.db_bootstrap"]
    error_message = "db-bootstrap runs backend/ops/db_bootstrap.py."
  }

  assert {
    condition     = strcontains(local.migrate_command[2], "manage.py db_preflight && python manage.py migrate")
    error_message = "migrate must prove the role is restricted (db_preflight) before migrating."
  }

  assert {
    condition     = !anytrue([for secret in local.app_secrets : strcontains(secret.valueFrom, "rds-master")])
    error_message = "Services and the migrate task run as the application role, never the master user."
  }
}
