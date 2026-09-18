# WAF request-body handling and logging regressions (phase 12 P0 remediation):
# - AWSManagedRulesCommonRuleSet's SizeRestrictions_BODY blocks every body over
#   8 KB, the most an ALB-attached web ACL can inspect. That blocked document
#   uploads (up to 25 MB), bank statement imports (CSV text in JSON) and any
#   large invoice or journal outright.
# - Nothing logged what the web ACL blocked or why.

mock_provider "aws" {
  override_during = plan
  source          = "./tests/mocks"
}

mock_provider "random" {}

variables {
  environment     = "staging"
  container_image = "123456789012.dkr.ecr.ap-south-1.amazonaws.com/easybook-staging-backend@sha256:0000000000000000000000000000000000000000000000000000000000000000"
}

run "large_authenticated_bodies_are_not_blocked_by_the_8kb_size_rule" {
  command = plan

  assert {
    condition = anytrue([
      for rule in aws_wafv2_web_acl.alb.rule : anytrue([
        for statement in rule.statement : anytrue([
          for group in statement.managed_rule_group_statement : group.name == "AWSManagedRulesCommonRuleSet" && anytrue([
            for override in group.rule_action_override : override.name == "SizeRestrictions_BODY" && length(override.action_to_use[0].count) == 1
          ])
        ])
      ])
    ])
    error_message = "SizeRestrictions_BODY must be overridden to COUNT, or every body over 8 KB is blocked."
  }
}

run "oversize_bodies_are_still_blocked_where_no_credentials_are_needed" {
  command = plan

  assert {
    condition = anytrue([
      for rule in aws_wafv2_web_acl.alb.rule : rule.name == "block-oversize-body-unauthenticated" && length(rule.action[0].block) == 1
    ])
    error_message = "Unauthenticated routes (auth, admin login) must block bodies WAF cannot fully inspect."
  }

  assert {
    condition = alltrue([
      for rule in aws_wafv2_web_acl.alb.rule : rule.priority < [
        for managed in aws_wafv2_web_acl.alb.rule : managed.priority if managed.name == "aws-managed-common"
      ][0] if rule.name == "block-oversize-body-unauthenticated"
    ])
    error_message = "The oversize-body block must run before the managed rule groups."
  }
}

run "web_acl_traffic_is_logged_with_credentials_redacted" {
  command = plan

  assert {
    condition     = startswith(aws_cloudwatch_log_group.waf.name, "aws-waf-logs-")
    error_message = "AWS WAF only delivers to log groups named aws-waf-logs-*."
  }

  assert {
    condition = toset([
      for field in aws_wafv2_web_acl_logging_configuration.alb.redacted_fields : field.single_header[0].name
    ]) == toset(["authorization", "cookie"])
    error_message = "Bearer tokens and session cookies must never land in WAF logs."
  }
}
