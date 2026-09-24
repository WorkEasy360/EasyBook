# GitHub Actions -> AWS, with no static keys (OIDC federation).
#
#  - release role (always created): assumable ONLY by workflows running on
#    `main` of var.github_repository. It may push images to this
#    environment's two ECR repositories and nothing else: no delete, no
#    other repository, no ECS, no IAM.
#  - deploy role (var.enable_github_deploy_role, default off): assumable ONLY
#    from the protected GitHub environment var.github_deploy_environment,
#    whose required reviewers are the approval gate. It may run this
#    environment's one-off migrate task and roll its own ECS services, and
#    pass only the two roles those task definitions use. Enable it only
#    after that environment exists with required reviewers.
#
# An AWS account can hold only one OIDC provider per issuer URL. If the
# account already has one for GitHub (another application may have created
# it), pass its ARN as var.github_oidc_provider_arn: this stack then only
# references it and never modifies it.

locals {
  github_oidc_url             = "token.actions.githubusercontent.com"
  create_github_oidc          = var.github_oidc_provider_arn == ""
  github_oidc_provider_arn    = local.create_github_oidc ? aws_iam_openid_connect_provider.github[0].arn : var.github_oidc_provider_arn
  github_release_subject      = "repo:${var.github_repository}:ref:refs/heads/main"
  github_deploy_subject       = "repo:${var.github_repository}:environment:${var.github_deploy_environment}"
  release_ecr_repository_arns = [aws_ecr_repository.backend.arn, aws_ecr_repository.frontend.arn]

  # Push-only: enough for `docker push` of a new tag. No BatchDeleteImage,
  # no lifecycle or policy changes, no other repository.
  release_ecr_push_actions = [
    "ecr:BatchCheckLayerAvailability",
    "ecr:BatchGetImage",
    "ecr:CompleteLayerUpload",
    "ecr:DescribeImages",
    "ecr:InitiateLayerUpload",
    "ecr:PutImage",
    "ecr:UploadLayerPart",
  ]
}

resource "aws_iam_openid_connect_provider" "github" {
  count          = local.create_github_oidc ? 1 : 0
  url            = "https://${local.github_oidc_url}"
  client_id_list = ["sts.amazonaws.com"]
}

data "aws_iam_policy_document" "github_release_trust" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [local.github_oidc_provider_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "${local.github_oidc_url}:aud"
      values   = ["sts.amazonaws.com"]
    }
    # Exact match, never StringLike: only the main branch of this repository.
    condition {
      test     = "StringEquals"
      variable = "${local.github_oidc_url}:sub"
      values   = [local.github_release_subject]
    }
  }
}

resource "aws_iam_role" "github_release" {
  name                 = "${local.name}-github-release"
  assume_role_policy   = data.aws_iam_policy_document.github_release_trust.json
  max_session_duration = 3600
}

data "aws_iam_policy_document" "github_release" {
  statement {
    sid       = "EcrLogin"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"] # this action has no resource-level scoping
  }

  statement {
    sid       = "PushReleaseImages"
    actions   = local.release_ecr_push_actions
    resources = local.release_ecr_repository_arns
  }
}

resource "aws_iam_role_policy" "github_release" {
  name   = "${local.name}-github-release"
  role   = aws_iam_role.github_release.id
  policy = data.aws_iam_policy_document.github_release.json
}

# --- Deploy role: created only when explicitly enabled ----------------------

data "aws_iam_policy_document" "github_deploy_trust" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [local.github_oidc_provider_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "${local.github_oidc_url}:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "${local.github_oidc_url}:sub"
      values   = [local.github_deploy_subject]
    }
  }
}

resource "aws_iam_role" "github_deploy" {
  count                = var.enable_github_deploy_role ? 1 : 0
  name                 = "${local.name}-github-deploy"
  assume_role_policy   = data.aws_iam_policy_document.github_deploy_trust.json
  max_session_duration = 3600
}

data "aws_iam_policy_document" "github_deploy" {
  statement {
    sid       = "RegisterTaskDefinitionRevisions"
    actions   = ["ecs:RegisterTaskDefinition", "ecs:DescribeTaskDefinition"]
    resources = ["*"] # these actions have no resource-level scoping
  }

  statement {
    sid     = "RollThisEnvironmentsServices"
    actions = ["ecs:UpdateService", "ecs:DescribeServices"]
    resources = concat(
      [for service in aws_ecs_service.service : service.id],
      [aws_ecs_service.web.id],
    )
  }

  statement {
    sid       = "RunTheMigrateTask"
    actions   = ["ecs:RunTask"]
    resources = ["arn:aws:ecs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:task-definition/${local.name}-migrate:*"]
    condition {
      test     = "ArnEquals"
      variable = "ecs:cluster"
      values   = [aws_ecs_cluster.main.arn]
    }
  }

  statement {
    sid       = "WatchTasks"
    actions   = ["ecs:DescribeTasks"]
    resources = ["arn:aws:ecs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:task/${aws_ecs_cluster.main.name}/*"]
  }

  statement {
    sid       = "PassOnlyTheTaskRoles"
    actions   = ["iam:PassRole"]
    resources = [aws_iam_role.ecs_execution.arn, aws_iam_role.ecs_task.arn]
    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role_policy" "github_deploy" {
  count  = var.enable_github_deploy_role ? 1 : 0
  name   = "${local.name}-github-deploy"
  role   = aws_iam_role.github_deploy[0].id
  policy = data.aws_iam_policy_document.github_deploy.json
}
