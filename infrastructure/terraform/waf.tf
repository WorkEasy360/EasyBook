# WAF directly in front of the ALB (REGIONAL scope), not CloudFront -> WAF ->
# ALB as phase 12 section 9's target diagram shows. Deliberate deviation,
# not an oversight: CloudFront's own value today would be caching/edge
# delivery for the Next.js frontend, and frontend/ has no build yet (root
# CLAUDE.md repo map) — there is nothing to cache or split /api/* vs /* for.
# A regional WAF Web ACL attached straight to the ALB is a fully AWS-supported
# architecture on its own and delivers the actual required control (managed-
# rule + rate-based protection in front of the API) without the cross-region
# (CloudFront Web ACLs must live in us-east-1 regardless of this stack's own
# region) and origin-bypass-prevention complexity a CloudFront hop would add
# for no current benefit. Add CloudFront (+ move this Web ACL's scope, or add
# a second one) once phase 12 section 74's frontend exists — see
# infrastructure/terraform/README.md.

resource "aws_wafv2_web_acl" "alb" {
  name  = "${local.name}-alb"
  scope = "REGIONAL"

  default_action {
    allow {}
  }

  rule {
    name     = "aws-managed-common"
    priority = 1
    override_action {
      none {}
    }
    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesCommonRuleSet"
        vendor_name = "AWS"
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${local.name}-common"
      sampled_requests_enabled   = true
    }
  }

  rule {
    name     = "aws-managed-known-bad-inputs"
    priority = 2
    override_action {
      none {}
    }
    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesKnownBadInputsRuleSet"
        vendor_name = "AWS"
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${local.name}-known-bad-inputs"
      sampled_requests_enabled   = true
    }
  }

  # Per-IP request-rate cap (phase 12 section 64) — a coarse edge-level
  # backstop, not a replacement for DRF's own per-scope throttles
  # (REST_FRAMEWORK.DEFAULT_THROTTLE_RATES, config/settings/base.py) which
  # already vary by endpoint class.
  rule {
    name     = "rate-limit-per-ip"
    priority = 3
    action {
      block {}
    }
    statement {
      rate_based_statement {
        limit              = 3000
        aggregate_key_type = "IP"
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${local.name}-rate-limit"
      sampled_requests_enabled   = true
    }
  }

  visibility_config {
    cloudwatch_metrics_enabled = true
    metric_name                = "${local.name}-alb"
    sampled_requests_enabled   = true
  }
}

resource "aws_wafv2_web_acl_association" "alb" {
  resource_arn = aws_lb.main.arn
  web_acl_arn  = aws_wafv2_web_acl.alb.arn
}
