# One cluster, one task definition + service per entry in var.ecs_services —
# all sharing the single image built from backend/Dockerfile (phase 12
# section 11). Every service is a near-identical shape (Fargate, awsvpc,
# private subnets, the same execution/task roles) except for command/size/
# count, so for_each over the map rather than six hand-copied resource
# blocks — this is exactly the kind of structural duplication root CLAUDE.md's
# "reuse existing abstractions... small, targeted changes" is about.

resource "aws_ecs_cluster" "main" {
  name = local.name

  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

resource "aws_cloudwatch_log_group" "ecs" {
  for_each          = var.ecs_services
  name              = "/ecs/${local.name}/${each.key}"
  retention_in_days = 30
}

locals {
  # ECS's own container healthCheck (NOT the Docker image's baked-in
  # HEALTHCHECK, which Fargate ignores entirely for scheduling decisions —
  # verified against AWS's ECS task definition documentation, 2026-09-16)
  # only makes sense for the one service with an HTTP port at all.
  container_health_check = {
    command     = ["CMD-SHELL", "python -c \"import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health/live/', timeout=3).status == 200 else 1)\""]
    interval    = 30
    timeout     = 5
    retries     = 3
    startPeriod = 15
  }
}

locals {
  # Shared by every service AND the one-off migrate task, so a migration runs
  # against exactly the configuration the services will run with.
  app_environment = [
    { name = "DB_NAME", value = aws_db_instance.main.db_name },
    { name = "DB_HOST", value = aws_db_instance.main.address },
    { name = "DB_PORT", value = tostring(aws_db_instance.main.port) },
    # rediss:// (TLS), matching elasticache.tf's transit_encryption_enabled.
    { name = "REDIS_URL", value = "rediss://${aws_elasticache_replication_group.main.primary_endpoint_address}:6379/0" },
    # One trusted hop: the ALB appends the connecting address to
    # X-Forwarded-For (backend/core/client_ip.py).
    { name = "TRUSTED_PROXY_COUNT", value = "1" },
    { name = "DOCUMENT_STORAGE_BACKEND", value = "s3" },
    { name = "DOCUMENT_STORAGE_S3_BUCKET", value = aws_s3_bucket.documents.bucket },
    { name = "DOCUMENT_STORAGE_S3_REGION", value = var.aws_region },
    # All three derive from the one required hostname (variables.tf).
    { name = "DJANGO_ALLOWED_HOSTS", value = var.domain_name },
    { name = "CSRF_TRUSTED_ORIGINS", value = "https://${var.domain_name}" },
    { name = "CORS_ALLOWED_ORIGINS", value = "https://${var.domain_name}" },
  ]

  # The application role's credentials only. The RDS master credential is
  # deliberately absent: only the one-off db-bootstrap task (below) can read
  # it, through its own execution role (iam.tf).
  app_secrets = [
    { name = "DJANGO_SECRET_KEY", valueFrom = aws_secretsmanager_secret.django_secret_key.arn },
    { name = "BFF_PROXY_SECRET", valueFrom = aws_secretsmanager_secret.bff_proxy_secret.arn },
    { name = "DB_USER", valueFrom = "${aws_secretsmanager_secret.app_db_credentials.arn}:username::" },
    { name = "DB_PASSWORD", valueFrom = "${aws_secretsmanager_secret.app_db_credentials.arn}:password::" },
  ]
}

resource "aws_ecs_task_definition" "service" {
  for_each = var.ecs_services

  family                   = "${local.name}-${each.key}"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = tostring(each.value.cpu)
  memory                   = tostring(each.value.memory)
  execution_role_arn       = aws_iam_role.ecs_execution.arn
  task_role_arn            = aws_iam_role.ecs_task.arn

  container_definitions = jsonencode([
    {
      name      = each.key
      image     = var.container_image
      essential = true
      command   = each.value.command

      portMappings = each.value.is_load_balanced ? [
        { containerPort = 8000, protocol = "tcp" }
      ] : []

      healthCheck = each.value.is_load_balanced ? local.container_health_check : null

      environment = concat(local.app_environment, [
        # The API keeps Django's 30s default (backend/config/settings/base.py);
        # workers run batch statements (recurring generation, retention
        # purges) that may legitimately take longer, but never unbounded.
        { name = "DB_STATEMENT_TIMEOUT_MS", value = each.value.is_load_balanced ? "30000" : "300000" },
      ])

      secrets = local.app_secrets

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.ecs[each.key].name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = each.key
        }
      }
    }
  ])
}

resource "aws_ecs_service" "service" {
  for_each = var.ecs_services

  name            = "${local.name}-${each.key}"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.service[each.key].arn
  desired_count   = each.value.desired_count
  launch_type     = "FARGATE"

  # Beat must never run 2 replicas even transiently during a rolling deploy —
  # a plain desired_count of 1 does not guarantee that on its own, since the
  # default deployment strategy scales up to `deployment_maximum_percent`
  # (200%) BEFORE scaling the old task down. Recreate instead: kill first,
  # then start (phase 12 section 11/18; see var.ecs_services' own comment).
  deployment_maximum_percent         = each.key == "beat" ? 100 : 200
  deployment_minimum_healthy_percent = each.key == "beat" ? 0 : 100

  # A deployment whose tasks never start or never pass health checks is
  # stopped and rolled back to the last COMPLETED deployment, instead of
  # replacing tasks forever. Failures are announced (cloudwatch.tf,
  # ecs_deployment_failed) so a rollback is never silent.
  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  # ELB health-check failures during container startup (migrations are not
  # run here, but Django import + gunicorn boot still take seconds) must not
  # count against a new task or the circuit breaker.
  health_check_grace_period_seconds = each.value.is_load_balanced ? 60 : null

  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.ecs_tasks.id]
    assign_public_ip = false
  }

  dynamic "load_balancer" {
    for_each = each.value.is_load_balanced ? [1] : []
    content {
      target_group_arn = aws_lb_target_group.api.arn
      container_name   = each.key
      container_port   = 8000
    }
  }

  # The ALB must exist and have a listener forwarding to the target group
  # before the api service's first deployment attempt, or ECS's own
  # target-group registration during service creation can race the listener.
  depends_on = [aws_lb_listener.https]
}

# --- Next.js frontend (frontend/Dockerfile) --------------------------------
# Serves every page and the BFF. It reaches Django through the same public
# HTTPS hostname and ALB as any client (API_BASE_URL), so TLS, host
# validation, the WAF and trusted-proxy handling are identical on both paths:
# the topology exercised end to end by the local production smoke test.

locals {
  web_environment = [
    { name = "API_BASE_URL", value = "https://${var.domain_name}/api/v1" },
    { name = "SESSION_COOKIE_SECURE", value = "1" },
    # One trusted hop in front of the frontend: the ALB.
    { name = "TRUSTED_PROXY_COUNT", value = "1" },
  ]
}

resource "aws_cloudwatch_log_group" "web" {
  name              = "/ecs/${local.name}/web"
  retention_in_days = 30
}

resource "aws_ecs_task_definition" "web" {
  family                   = "${local.name}-web"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = tostring(var.web_service.cpu)
  memory                   = tostring(var.web_service.memory)
  execution_role_arn       = aws_iam_role.ecs_execution.arn
  # No task role: the frontend calls no AWS API.

  container_definitions = jsonencode([
    {
      name         = "web"
      image        = var.frontend_container_image
      essential    = true
      portMappings = [{ containerPort = 3000, protocol = "tcp" }]

      healthCheck = {
        command     = ["CMD", "node", "-e", "fetch('http://127.0.0.1:3000/api/health').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"]
        interval    = 30
        timeout     = 5
        retries     = 3
        startPeriod = 20
      }

      # Checked at start-up (frontend/src/instrumentation.ts): a missing or
      # unsafe value stops the task instead of serving insecurely.
      environment = local.web_environment
      secrets = [
        { name = "BFF_PROXY_SECRET", valueFrom = aws_secretsmanager_secret.bff_proxy_secret.arn },
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.web.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "web"
        }
      }
    }
  ])
}

resource "aws_ecs_service" "web" {
  name            = "${local.name}-web"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.web.arn
  desired_count   = var.web_service.desired_count
  launch_type     = "FARGATE"

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  health_check_grace_period_seconds = 60

  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.ecs_tasks.id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.web.arn
    container_name   = "web"
    container_port   = 3000
  }

  depends_on = [aws_lb_listener.https]
}
