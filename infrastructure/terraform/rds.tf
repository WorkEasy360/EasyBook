# RDS PostgreSQL 16 with pgvector (phase 12 section 13-14). Two things
# Terraform deliberately does NOT do, both because it has no network path to
# a private-subnet database from wherever `terraform apply` runs, and both
# documented as one-time post-apply steps in README.md:
#   1. `CREATE EXTENSION vector;` — must run as the RDS master user (the
#      app's own role, created in step 2, cannot: pgvector is untrusted).
#      backend/ai/checks.py's pre_migrate hook fails closed if this is skipped.
#   2. `CREATE ROLE easybook_app ...` — the non-superuser role the app
#      actually connects as (secrets.tf generates its password already).
# The master credential itself (managed_master_user_password) is an
# AWS-managed Secrets Manager secret — nobody, including this Terraform
# config, ever sees the master password in plaintext.

resource "aws_db_subnet_group" "main" {
  name       = "${local.name}-db"
  subnet_ids = aws_subnet.private[*].id
}

resource "aws_db_parameter_group" "postgres16" {
  name   = "${local.name}-postgres16"
  family = "postgres16"

  # Slow-query monitoring (phase 12 section 13) — logs anything over 1s.
  # Tune once real traffic gives a baseline; this is a starting point, not a
  # load-tested threshold.
  parameter {
    name  = "log_min_duration_statement"
    value = "1000"
  }
}

resource "aws_db_instance" "main" {
  identifier     = "${local.name}-db"
  engine         = "postgres"
  engine_version = var.db_engine_version

  instance_class        = var.db_instance_class
  allocated_storage     = var.db_allocated_storage_gb
  max_allocated_storage = var.db_max_allocated_storage_gb
  storage_type          = "gp3"
  storage_encrypted     = true

  db_name  = var.db_name
  username = "easybook_admin" # RDS master user — never the app's runtime role (global rule 4).

  manage_master_user_password = true

  db_subnet_group_name   = aws_db_subnet_group.main.name
  parameter_group_name   = aws_db_parameter_group.postgres16.name
  vpc_security_group_ids = [aws_security_group.rds.id]
  publicly_accessible    = false

  multi_az                = var.db_multi_az
  backup_retention_period = var.db_backup_retention_days
  backup_window           = "17:00-18:00" # 22:30-23:30 IST — outside the 09:00-19:00 IST business hours a GST-focused product should expect its heaviest load in
  maintenance_window      = "sun:18:00-sun:19:00"

  deletion_protection       = var.db_deletion_protection
  skip_final_snapshot       = false
  final_snapshot_identifier = "${local.name}-db-final-${formatdate("YYYYMMDD-hhmmss", timestamp())}"

  auto_minor_version_upgrade = true

  lifecycle {
    # A snapshot identifier baked from timestamp() would otherwise force a
    # replace on every plan; the resource itself never needs recreating over
    # that value changing.
    ignore_changes = [final_snapshot_identifier]
  }
}
