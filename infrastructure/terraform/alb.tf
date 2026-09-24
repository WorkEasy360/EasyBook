# Public ALB (phase 12 section 7/9), HTTPS only, one hostname
# (var.domain_name). /api/v1/* goes to the Django API, everything else to the
# Next.js frontend (web service, ecs.tf). Next's own /api/bff, /api/auth and
# /api/health routes stay on the frontend because only /api/v1/* is routed to
# Django. Django admin (/admin/) is deliberately not routed publicly.

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
    # Liveness, never readiness. Probing /api/v1/health/ (database + Redis)
    # meant one Redis or database blip failed EVERY target at once — the ALB
    # then had nothing healthy to route to and ECS replaced tasks that were
    # fine, turning a brief dependency blip into a full outage. Dependency
    # health is for monitoring and alerts (cloudwatch.tf), not for routing or
    # replacement. core/middleware.py answers this path before host
    # validation, since the ALB sends the task IP as Host.
    path                = "/api/v1/health/live/"
    matcher             = "200"
    interval            = 30
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }

  deregistration_delay = 30
}

resource "aws_lb_target_group" "web" {
  name        = "${local.name}-web"
  port        = 3000
  protocol    = "HTTP"
  vpc_id      = aws_vpc.main.id
  target_type = "ip"

  health_check {
    # Liveness only (frontend/src/app/api/health/route.ts): never depends on
    # the API, so an API outage cannot get healthy frontend tasks replaced.
    path                = "/api/health"
    matcher             = "200"
    interval            = 30
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }

  deregistration_delay = 30
}

# HTTPS is not optional. With no certificate the previous design forwarded
# plaintext HTTP while Django (SECURE_SSL_REDIRECT) redirected every request
# to an HTTPS port nothing listened on: an environment that served nothing.
# var.domain_name and var.acm_certificate_arn are now required (variables.tf).
resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.main.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = var.acm_certificate_arn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.web.arn
  }
}

resource "aws_lb_listener_rule" "api" {
  listener_arn = aws_lb_listener.https.arn
  priority     = 10

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api.arn
  }

  condition {
    path_pattern {
      values = ["/api/v1/*"]
    }
  }
}

# Port 80 only ever redirects to HTTPS.
resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.main.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type = "redirect"

    redirect {
      port        = "443"
      protocol    = "HTTPS"
      status_code = "HTTP_301"
    }
  }
}
