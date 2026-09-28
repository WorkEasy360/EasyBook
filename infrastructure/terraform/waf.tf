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

  # REQUEST BODIES. An ALB-attached web ACL inspects only the first 8 KB of a
  # body (fixed; CloudFront/API Gateway can go to 64 KB). The managed body rules
  # below inspect those 8 KB with oversize handling CONTINUE.
  #
  # The Common rule set's SizeRestrictions_BODY blocks every body over 8 KB,
  # which blocked legitimate authenticated traffic outright: document uploads
  # (up to 25 MB), bank statement imports (CSV text in JSON), large invoices and
  # journals. It is overridden to COUNT (the label still applies), and size is
  # enforced where the request is understood: Django's DATA_UPLOAD_MAX_MEMORY_SIZE
  # and the per-category document limits (DOCUMENT_MAX_UPLOAD_SIZES).
  #
  # Where no credentials are needed — sign-in, registration, token refresh and
  # the Django admin login — no legitimate body comes near 8 KB, so anything
  # WAF cannot fully inspect is blocked there, before any managed rule runs.
  rule {
    name     = "block-oversize-body-unauthenticated"
    priority = 0
    action {
      block {}
    }
    statement {
      and_statement {
        statement {
          size_constraint_statement {
            comparison_operator = "GT"
            size                = 8192
            field_to_match {
              body {
                oversize_handling = "MATCH"
              }
            }
            text_transformation {
              priority = 0
              type     = "NONE"
            }
          }
        }
        statement {
          or_statement {
            statement {
              byte_match_statement {
                positional_constraint = "STARTS_WITH"
                search_string         = "/api/v1/auth/"
                field_to_match {
                  uri_path {}
                }
                text_transformation {
                  priority = 0
                  type     = "NONE"
                }
              }
            }
            statement {
              byte_match_statement {
                positional_constraint = "STARTS_WITH"
                search_string         = "/admin/"
                field_to_match {
                  uri_path {}
                }
                text_transformation {
                  priority = 0
                  type     = "NONE"
                }
              }
            }
          }
        }
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${local.name}-oversize-body-unauthenticated"
      sampled_requests_enabled   = true
    }
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

        rule_action_override {
          name = "SizeRestrictions_BODY"
          action_to_use {
            count {}
          }
        }
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

        # The frontend's server-side calls to /api/v1 leave the VPC through
        # the NAT gateway(s) and come back through this ALB, so every user's
        # API traffic shares a handful of NAT addresses: counted per IP they
        # would trip this limit together. They are exempted here; Django
        # still throttles each real client by the address the BFF asserts
        # with its shared secret (backend/core/client_ip.py). Browser
        # requests to the frontend itself keep this per-IP limit.
        scope_down_statement {
          not_statement {
            statement {
              ip_set_reference_statement {
                arn = aws_wafv2_ip_set.nat_egress.arn
              }
            }
          }
        }
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

# Traffic logs: what the web ACL allowed, counted and blocked, and why (the
# terminating rule and labels). Tokens and session cookies are redacted before
# AWS WAF writes the record; nothing else in a request here is a credential.
resource "aws_cloudwatch_log_group" "waf" {
  # AWS WAF only delivers to log groups whose name starts with aws-waf-logs-.
  name              = "aws-waf-logs-${local.name}"
  retention_in_days = 90
}

# Managed explicitly: otherwise AWS WAF edits the account-wide AWSWAF-LOGS
# resource policy itself, which can exceed its size limit and fail the apply
# (terraform-provider-aws docs, aws_wafv2_web_acl_logging_configuration).
data "aws_iam_policy_document" "waf_logs" {
  statement {
    sid       = "AWSWAFLogDelivery"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.waf.arn}:*"]

    principals {
      type        = "Service"
      identifiers = ["delivery.logs.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }

    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values   = ["arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:*"]
    }
  }
}

resource "aws_cloudwatch_log_resource_policy" "waf_logs" {
  policy_name     = "${local.name}-waf-logs"
  policy_document = data.aws_iam_policy_document.waf_logs.json
}

resource "aws_wafv2_web_acl_logging_configuration" "alb" {
  resource_arn            = aws_wafv2_web_acl.alb.arn
  log_destination_configs = [aws_cloudwatch_log_group.waf.arn]

  redacted_fields {
    single_header {
      name = "authorization"
    }
  }

  redacted_fields {
    single_header {
      name = "cookie"
    }
  }

  depends_on = [aws_cloudwatch_log_resource_policy.waf_logs]
}

resource "aws_wafv2_ip_set" "nat_egress" {
  name               = "${local.name}-nat-egress"
  scope              = "REGIONAL"
  ip_address_version = "IPV4"
  addresses          = [for eip in aws_eip.nat : "${eip.public_ip}/32"]
}
