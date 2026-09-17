# ElastiCache Redis, private-subnet only (phase 12 section 16). Used for
# Celery broker/results, DRF rate limiting, and cache — never authoritative
# financial storage (root CLAUDE.md / phase 12 section 16).

resource "aws_elasticache_subnet_group" "main" {
  name       = "${local.name}-redis"
  subnet_ids = aws_subnet.private[*].id
}

resource "aws_elasticache_replication_group" "main" {
  replication_group_id = "${local.name}-redis"
  description          = "EasyBook Celery broker/results + cache"

  engine         = "redis"
  engine_version = "7.1"
  node_type      = var.redis_node_type
  port           = 6379

  num_cache_clusters         = var.redis_multi_az ? 2 : 1
  automatic_failover_enabled = var.redis_multi_az
  multi_az_enabled           = var.redis_multi_az

  subnet_group_name  = aws_elasticache_subnet_group.main.name
  security_group_ids = [aws_security_group.redis.id]

  at_rest_encryption_enabled = true
  transit_encryption_enabled = true

  auto_minor_version_upgrade = true

  # No AUTH token by default: the security boundary here is the security
  # group (Redis is unreachable from outside the ECS tasks SG at the network
  # layer already). Add one (aws_elasticache_replication_group.auth_token,
  # backed by a Secrets Manager entry like secrets.tf's others) if defense in
  # depth against a compromised same-VPC resource becomes a requirement.
}
