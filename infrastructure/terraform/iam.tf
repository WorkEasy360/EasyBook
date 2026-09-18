# Two roles, least-privilege (phase 12 section 7/72):
#  - Execution role: what ECS/Fargate itself needs to START a task (pull the
#    image, write logs, resolve the `secrets` block in each container
#    definition into environment variables before the app's own code runs).
#  - Task role: what the APPLICATION's own AWS SDK (boto3) calls need once
#    running — currently just the documents S3 bucket.
# Neither role is granted anything beyond what ecs.tf's task definitions
# actually reference — no wildcard resource ARNs on secretsmanager/s3.

data "aws_iam_policy_document" "ecs_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "ecs_execution" {
  name               = "${local.name}-ecs-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume_role.json
}

resource "aws_iam_role_policy_attachment" "ecs_execution_managed" {
  role       = aws_iam_role.ecs_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

data "aws_iam_policy_document" "ecs_execution_secrets" {
  statement {
    sid     = "ReadTaskSecrets"
    actions = ["secretsmanager:GetSecretValue"]
    resources = concat(
      [
        aws_secretsmanager_secret.django_secret_key.arn,
        aws_secretsmanager_secret.bff_proxy_secret.arn,
        aws_secretsmanager_secret.app_db_credentials.arn,
        aws_db_instance.main.master_user_secret[0].secret_arn,
      ],
      [for s in aws_secretsmanager_secret.empty : s.arn],
    )
  }
}

resource "aws_iam_role_policy" "ecs_execution_secrets" {
  name   = "${local.name}-ecs-execution-secrets"
  role   = aws_iam_role.ecs_execution.id
  policy = data.aws_iam_policy_document.ecs_execution_secrets.json
}

resource "aws_iam_role" "ecs_task" {
  name               = "${local.name}-ecs-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume_role.json
}

data "aws_iam_policy_document" "ecs_task_s3_documents" {
  statement {
    sid       = "DocumentsBucketObjectAccess"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.documents.arn}/*"]
  }

  statement {
    sid       = "DocumentsBucketListing"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.documents.arn]
  }
}

resource "aws_iam_role_policy" "ecs_task_s3_documents" {
  name   = "${local.name}-ecs-task-s3-documents"
  role   = aws_iam_role.ecs_task.id
  policy = data.aws_iam_policy_document.ecs_task_s3_documents.json
}
