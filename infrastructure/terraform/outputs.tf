output "alb_dns_name" {
  description = "The ALB's own DNS name: the target of var.domain_name's CNAME/ALIAS record. Not usable directly: the certificate and Django's ALLOWED_HOSTS cover only var.domain_name."
  value       = aws_lb.main.dns_name
}

output "ecr_repository_url" {
  value = aws_ecr_repository.backend.repository_url
}

output "frontend_ecr_repository_url" {
  value = aws_ecr_repository.frontend.repository_url
}

output "app_url" {
  description = "Point var.domain_name's DNS (CNAME/ALIAS) at alb_dns_name; the app is then served here."
  value       = "https://${var.domain_name}"
}

output "rds_endpoint" {
  value = aws_db_instance.main.address
}

output "rds_master_user_secret_arn" {
  description = "AWS-managed Secrets Manager secret holding the RDS master password. Only the db-bootstrap task definition's execution role can read it (runbooks/database-bootstrap.md)."
  value       = aws_db_instance.main.master_user_secret[0].secret_arn
}

output "app_db_credentials_secret_arn" {
  description = "Secrets Manager secret holding the app's own (non-superuser) DB role's generated password — needed for the same one-time setup."
  value       = aws_secretsmanager_secret.app_db_credentials.arn
  sensitive   = false # the secret value itself is not exposed, only which secret to go read
}

output "redis_primary_endpoint" {
  value = aws_elasticache_replication_group.main.primary_endpoint_address
}

output "documents_bucket_name" {
  value = aws_s3_bucket.documents.bucket
}

output "ecs_cluster_name" {
  value = aws_ecs_cluster.main.name
}

output "db_bootstrap_task_definition_arn" {
  description = "One-off task: run ONCE per environment before the first migration (runbooks/database-bootstrap.md)."
  value       = aws_ecs_task_definition.db_bootstrap.arn
}

output "migrate_task_definition_arn" {
  description = "One-off task: run before every release's service deployment (runbooks/database-bootstrap.md)."
  value       = aws_ecs_task_definition.migrate.arn
}

output "oneoff_task_network" {
  description = "awsvpcConfiguration for `aws ecs run-task` of the one-off tasks: private subnets, the tasks security group, no public IP."
  value = {
    subnets        = aws_subnet.private[*].id
    securityGroups = [aws_security_group.ecs_tasks.id]
    assignPublicIp = "DISABLED"
  }
}

output "github_release_role_arn" {
  description = "Set as the GitHub repository variable AWS_RELEASE_ROLE_ARN (with AWS_REGION, ECR_BACKEND_REPOSITORY and ECR_FRONTEND_REPOSITORY) to enable image publishing from main."
  value       = aws_iam_role.github_release.arn
}
