# CI deploy identity (P0 remediation): no image ever reached ECR because CI
# had no identity to push with. The release role must be keyless (OIDC),
# assumable only from main of this repository, and able to push images and
# nothing else. The deploy role must not exist until explicitly enabled.

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

run "release_role_trusts_only_main_of_this_repository" {
  command = plan

  assert {
    condition     = local.github_release_subject == "repo:WorkEasy360/EasyBook:ref:refs/heads/main"
    error_message = "Only workflows on main of WorkEasy360/EasyBook may assume the release role."
  }

  assert {
    condition = alltrue([
      for s in data.aws_iam_policy_document.github_release_trust.statement : alltrue([
        for c in s.condition : c.test == "StringEquals"
      ])
    ])
    error_message = "Trust conditions must be exact (StringEquals), never wildcard (StringLike)."
  }
}

run "release_role_can_only_push_images" {
  command = plan

  assert {
    condition = alltrue([
      for action in local.release_ecr_push_actions : startswith(action, "ecr:") && !contains(["ecr:BatchDeleteImage", "ecr:DeleteRepository", "ecr:SetRepositoryPolicy", "ecr:PutLifecyclePolicy"], action)
    ])
    error_message = "The release role may push to ECR only: no delete, no policy changes."
  }

  assert {
    condition     = length(local.release_ecr_repository_arns) == 2
    error_message = "The release role reaches exactly the backend and frontend repositories."
  }
}

run "deploy_role_is_off_by_default" {
  command = plan

  assert {
    condition     = length(aws_iam_role.github_deploy) == 0
    error_message = "Automatic deployment is not enabled yet: the deploy role must not exist by default."
  }

  assert {
    condition     = local.github_deploy_subject == "repo:WorkEasy360/EasyBook:environment:staging"
    error_message = "When enabled, only the protected GitHub environment may assume the deploy role."
  }
}

run "an_existing_oidc_provider_is_reused_not_recreated" {
  command = plan

  variables {
    github_oidc_provider_arn = "arn:aws:iam::123456789012:oidc-provider/token.actions.githubusercontent.com"
  }

  assert {
    condition     = length(aws_iam_openid_connect_provider.github) == 0
    error_message = "With an existing provider ARN, this stack must not create (or touch) another one."
  }
}
