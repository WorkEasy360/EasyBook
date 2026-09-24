# One-off ECS task definitions — run with `aws ecs run-task`, never as
# services (infrastructure/runbooks/database-bootstrap.md). Both reuse the
# single backend image; neither publishes a port.
#
#  - db-bootstrap: ONCE per environment, before the first migration. The only
#    task definition allowed to read the RDS master credential, through its
#    own execution role (iam.tf). Runs backend/ops/db_bootstrap.py: pgvector,
#    the non-superuser application role, its grants, then verifies the role is
#    NOSUPERUSER/NOBYPASSRLS. No psql in any image, no bastion, no ECS Exec.
#  - migrate: every release, before the services roll. Runs as the
#    application role, first proving it is restricted (manage.py
#    db_preflight), with the statement timeout disabled for long DDL.

locals {
  db_bootstrap_command = ["python", "-m", "ops.db_bootstrap"]
  # db_preflight first: a migration run as a superuser/BYPASSRLS role would
  # "work" and then every service would refuse to boot against it.
  migrate_command = ["sh", "-c", "python manage.py db_preflight && python manage.py migrate --noinput"]
}

resource "aws_cloudwatch_log_group" "oneoff" {
  for_each          = toset(["db-bootstrap", "migrate"])
  name              = "/ecs/${local.name}/${each.key}"
  retention_in_days = 30
}

resource "aws_ecs_task_definition" "db_bootstrap" {
  family                   = "${local.name}-db-bootstrap"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "256"
  memory                   = "512"
  execution_role_arn       = aws_iam_role.db_bootstrap_execution.arn
  # No task role: the script calls no AWS API.

  container_definitions = jsonencode([
    {
      name      = "db-bootstrap"
      image     = var.container_image
      essential = true
      command   = local.db_bootstrap_command

      environment = [
        { name = "DB_NAME", value = aws_db_instance.main.db_name },
        { name = "DB_HOST", value = aws_db_instance.main.address },
        { name = "DB_PORT", value = tostring(aws_db_instance.main.port) },
        { name = "DB_SSLMODE", value = "require" },
      ]

      secrets = [
        { name = "DB_MASTER_USER", valueFrom = "${aws_db_instance.main.master_user_secret[0].secret_arn}:username::" },
        { name = "DB_MASTER_PASSWORD", valueFrom = "${aws_db_instance.main.master_user_secret[0].secret_arn}:password::" },
        { name = "DB_APP_USER", valueFrom = "${aws_secretsmanager_secret.app_db_credentials.arn}:username::" },
        { name = "DB_APP_PASSWORD", valueFrom = "${aws_secretsmanager_secret.app_db_credentials.arn}:password::" },
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.oneoff["db-bootstrap"].name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "db-bootstrap"
        }
      }
    }
  ])
}

resource "aws_ecs_task_definition" "migrate" {
  family                   = "${local.name}-migrate"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "512"
  memory                   = "1024"
  execution_role_arn       = aws_iam_role.ecs_execution.arn
  # No task role: migrations call no AWS API.

  container_definitions = jsonencode([
    {
      name      = "migrate"
      image     = var.container_image
      essential = true
      command   = local.migrate_command

      environment = concat(local.app_environment, [
        # DDL on a large table can legitimately exceed the API's 30s budget.
        { name = "DB_STATEMENT_TIMEOUT_MS", value = "0" },
      ])
      secrets = local.app_secrets

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.oneoff["migrate"].name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "migrate"
        }
      }
    }
  ])
}
