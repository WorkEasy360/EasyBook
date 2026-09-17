output "alb_dns_name" {
  description = "Point DNS (or use directly for smoke-testing before a domain exists) at this."
  value       = aws_lb.main.dns_name
}

output "ecr_repository_url" {
  value = aws_ecr_repository.backend.repository_url
}

output "rds_endpoint" {
  value = aws_db_instance.main.address
}

output "rds_master_user_secret_arn" {
  description = "AWS-managed Secrets Manager secret holding the RDS master password — needed once, for the one-time post-apply setup in README.md."
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
