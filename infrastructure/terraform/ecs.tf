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

      environment = [
        { name = "DB_NAME", value = aws_db_instance.main.db_name },
        { name = "DB_HOST", value = aws_db_instance.main.address },
        { name = "DB_PORT", value = tostring(aws_db_instance.main.port) },
        # rediss:// (TLS), matching elasticache.tf's transit_encryption_enabled.
        { name = "REDIS_URL", value = "rediss://${aws_elasticache_replication_group.main.primary_endpoint_address}:6379/0" },
        { name = "DOCUMENT_STORAGE_BACKEND", value = "s3" },
        { name = "DOCUMENT_STORAGE_S3_BUCKET", value = aws_s3_bucket.documents.bucket },
        { name = "DOCUMENT_STORAGE_S3_REGION", value = var.aws_region },
        { name = "DJANGO_ALLOWED_HOSTS", value = var.domain_name },
        { name = "CSRF_TRUSTED_ORIGINS", value = var.domain_name != "" ? "https://${var.domain_name}" : "" },
        {
          name  = "CORS_ALLOWED_ORIGINS"
          value = var.frontend_origin_domain != "" ? var.frontend_origin_domain : (var.domain_name != "" ? "https://${var.domain_name}" : "")
        },
      ]

      secrets = [
        { name = "DJANGO_SECRET_KEY", valueFrom = aws_secretsmanager_secret.django_secret_key.arn },
        { name = "DB_USER", valueFrom = "${aws_secretsmanager_secret.app_db_credentials.arn}:username::" },
        { name = "DB_PASSWORD", valueFrom = "${aws_secretsmanager_secret.app_db_credentials.arn}:password::" },
      ]

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
  depends_on = [aws_lb_listener.http]
}
