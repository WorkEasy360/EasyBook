# Public ALB (phase 12 section 7/9). Routes only to the "api" ECS service —
# there is no frontend/Next.js deployment yet (root CLAUDE.md repo map:
# frontend/ has no build), so CloudFront (cloudfront.tf) has nothing but this
# ALB to originate from for now; wire a second origin/behavior there once
# phase 12 section 74's frontend work exists.

resource "aws_lb" "main" {
  name               = "${local.name}-alb"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = aws_subnet.public[*].id

  enable_deletion_protection = var.environment == "production"
}

resource "aws_lb_target_group" "api" {
  name        = "${local.name}-api"
  port        = 8000
  protocol    = "HTTP"
  vpc_id      = aws_vpc.main.id
  target_type = "ip" # required for awsvpc-networked Fargate tasks

  health_check {
    # The readiness endpoint (DB + cache), not liveness: a target that can't
    # actually serve a request should stop receiving traffic even if the
    # process itself is alive (core/views.py:HealthCheckView vs LivenessCheckView).
    path                = "/api/v1/health/"
    matcher             = "200"
    interval            = 30
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }

  deregistration_delay = 30
}

# HTTPS listener only exists once a certificate is issued (var.acm_certificate_arn)
# — a business/DNS decision this file cannot make for you (phase 12 section 9).
resource "aws_lb_listener" "https" {
  count             = var.acm_certificate_arn != "" ? 1 : 0
  load_balancer_arn = aws_lb.main.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = var.acm_certificate_arn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api.arn
  }
}

# Before a certificate exists this forwards plaintext HTTP directly (so the
# stack is smoke-testable end to end from day one); once var.acm_certificate_arn
# is set it switches to a 301 redirect to HTTPS instead, matching
# config/settings/production.py's own SECURE_SSL_REDIRECT expectation.
resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.main.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type             = var.acm_certificate_arn != "" ? "redirect" : "forward"
    target_group_arn = var.acm_certificate_arn != "" ? null : aws_lb_target_group.api.arn

    dynamic "redirect" {
      for_each = var.acm_certificate_arn != "" ? [1] : []
      content {
        port        = "443"
        protocol    = "HTTPS"
        status_code = "HTTP_301"
      }
    }
  }
}
