# HTTPS-only hostname topology (P0 remediation). With the documented
# defaults the environment served nothing: no certificate meant no HTTPS
# listener, the HTTP listener forwarded plaintext, and Django redirected every
# request to an HTTPS port nothing listened on. There was also no frontend
# deployment at all.

mock_provider "aws" {
  override_during = plan
  source          = "./tests/mocks"
}

mock_provider "random" {}

variables {
  environment              = "staging"
  container_image          = "123456789012.dkr.ecr.ap-south-1.amazonaws.com/easybook-staging-backend@sha256:0000000000000000000000000000000000000000000000000000000000000000"
  frontend_container_image = "123456789012.dkr.ecr.ap-south-1.amazonaws.com/easybook-staging-frontend@sha256:0000000000000000000000000000000000000000000000000000000000000000"
  domain_name              = "staging.books.example.com"
  acm_certificate_arn      = "arn:aws:acm:ap-south-1:123456789012:certificate/00000000-0000-0000-0000-000000000000"
}

run "https_is_always_served_and_http_only_redirects" {
  command = plan

  assert {
    condition     = aws_lb_listener.https.protocol == "HTTPS" && aws_lb_listener.https.certificate_arn == var.acm_certificate_arn
    error_message = "The HTTPS listener must always exist, with the supplied certificate."
  }

  assert {
    condition     = aws_lb_listener.http.default_action[0].type == "redirect" && aws_lb_listener.http.default_action[0].redirect[0].protocol == "HTTPS"
    error_message = "Port 80 must only redirect to HTTPS, never forward plaintext."
  }

  assert {
    condition = length(aws_lb_listener_rule.api.condition) == 1 && alltrue([
      for c in aws_lb_listener_rule.api.condition : alltrue([for p in c.path_pattern : toset(p.values) == toset(["/api/v1/*"])])
    ])
    error_message = "Only /api/v1/* is routed to Django; the frontend keeps its own /api/* routes and /admin/ is not public."
  }
}

run "every_origin_setting_derives_from_the_one_hostname" {
  command = plan

  assert {
    condition = alltrue([
      for pair in [
        ["DJANGO_ALLOWED_HOSTS", "staging.books.example.com"],
        ["CSRF_TRUSTED_ORIGINS", "https://staging.books.example.com"],
        ["CORS_ALLOWED_ORIGINS", "https://staging.books.example.com"],
      ] : contains([for e in local.app_environment : e.value if e.name == pair[0]], pair[1])
    ])
    error_message = "ALLOWED_HOSTS, CSRF_TRUSTED_ORIGINS and CORS must come from var.domain_name, over HTTPS."
  }

  assert {
    condition = alltrue([
      contains([for e in local.web_environment : e.value if e.name == "API_BASE_URL"], "https://staging.books.example.com/api/v1"),
      contains([for e in local.web_environment : e.value if e.name == "SESSION_COOKIE_SECURE"], "1"),
    ])
    error_message = "The frontend must call the API over HTTPS on the same hostname and set Secure cookies."
  }
}

run "frontend_service_is_deployed_behind_the_alb" {
  command = plan

  assert {
    condition     = aws_lb_target_group.web.port == 3000 && aws_lb_target_group.web.health_check[0].path == "/api/health"
    error_message = "The frontend target group must probe the frontend's own liveness route."
  }

  assert {
    condition     = aws_ecs_service.web.deployment_circuit_breaker[0].enable && aws_ecs_service.web.deployment_circuit_breaker[0].rollback
    error_message = "The frontend service needs the deployment circuit breaker with rollback, like every backend service."
  }

  assert {
    condition     = aws_ecr_repository.frontend.image_tag_mutability == "IMMUTABLE"
    error_message = "Frontend image tags must be immutable."
  }
}

run "nat_egress_is_exempt_from_the_per_ip_rate_rule" {
  command = plan

  assert {
    condition = length([
      for rule in aws_wafv2_web_acl.alb.rule : rule
      if rule.name == "rate-limit-per-ip" && length(rule.statement[0].rate_based_statement[0].scope_down_statement) == 1
    ]) == 1
    error_message = "The per-IP rate rule must exclude the NAT egress addresses the BFF's API calls come from."
  }
}

run "missing_hostname_is_rejected" {
  command = plan

  variables {
    domain_name = ""
  }

  expect_failures = [var.domain_name]
}

run "hostname_with_scheme_or_wildcard_is_rejected" {
  command = plan

  variables {
    domain_name = "https://*.books.example.com"
  }

  expect_failures = [var.domain_name]
}

run "missing_certificate_is_rejected" {
  command = plan

  variables {
    acm_certificate_arn = ""
  }

  expect_failures = [var.acm_certificate_arn]
}
