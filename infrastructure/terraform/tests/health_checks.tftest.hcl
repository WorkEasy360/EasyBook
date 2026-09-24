# Health-check and deployment-safety regressions (phase 12 P0 remediation):
# - the ALB target group probed the readiness endpoint (database + Redis), so
#   a Redis or database blip marked every API task unhealthy at once and the
#   ALB had nothing healthy to route to — a dependency blip became a full
#   outage;
# - no ECS deployment circuit breaker: a bad image kept replacing tasks
#   forever instead of stopping and rolling back.

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

run "alb_probes_liveness_only" {
  command = plan

  assert {
    condition     = aws_lb_target_group.api.health_check[0].path == "/api/v1/health/live/"
    error_message = "The ALB must probe liveness, never readiness: a dependency blip would otherwise fail every target at once."
  }
}

run "every_service_rolls_back_a_failed_deployment" {
  command = plan

  assert {
    condition = alltrue([
      for service in aws_ecs_service.service :
      service.deployment_circuit_breaker[0].enable && service.deployment_circuit_breaker[0].rollback
    ])
    error_message = "Every ECS service needs the deployment circuit breaker with rollback."
  }
}

run "load_balanced_service_gets_a_startup_grace_period" {
  command = plan

  assert {
    condition     = aws_ecs_service.service["api"].health_check_grace_period_seconds >= 60
    error_message = "The api service must not count ALB health-check failures during container startup."
  }
}

run "failed_deployments_are_alerted" {
  command = plan

  assert {
    condition     = strcontains(aws_cloudwatch_event_rule.ecs_deployment_failed.event_pattern, "SERVICE_DEPLOYMENT_FAILED")
    error_message = "A rolled-back deployment must notify someone, not fail silently."
  }
}
