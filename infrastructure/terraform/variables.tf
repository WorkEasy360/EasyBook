# Every value here is either a safe, documented default or REQUIRED with no
# default (Terraform will prompt/fail rather than silently guess) — see each
# variable's description for which decisions are still the product/business's
# to make (phase 12 section 3: classify before building).

variable "project_name" {
  description = "Short slug used as a prefix for every resource name and tag."
  type        = string
  default     = "easybook"
}

variable "environment" {
  description = "Deployment environment name (staging, production, ...). Never share resources across environments (root CLAUDE.md global rule 1)."
  type        = string

  validation {
    condition     = contains(["staging", "production"], var.environment)
    error_message = "environment must be \"staging\" or \"production\" — a separate tfvars/workspace per environment, never a shared one (phase 12 section 5)."
  }
}

variable "aws_region" {
  description = "AWS region. Defaults to Mumbai (ap-south-1) — EasyBook is a GST/India-compliance-focused product; override if the business targets a different primary region."
  type        = string
  default     = "ap-south-1"
}

variable "vpc_cidr" {
  description = "CIDR block for the VPC."
  type        = string
  default     = "10.20.0.0/16"
}

variable "availability_zone_count" {
  description = "Number of AZs to spread public/private subnets across. 2 is the minimum for anything calling itself highly available."
  type        = number
  default     = 2

  validation {
    condition     = var.availability_zone_count >= 2
    error_message = "At least 2 AZs are required for HA — a single-AZ deployment is not production-grade."
  }
}

variable "single_nat_gateway" {
  description = "true = one NAT gateway shared by all private subnets (cheaper, single point of failure for outbound internet from private subnets). false = one NAT gateway per AZ (production-recommended, costs more). Decide per environment — staging can reasonably use true."
  type        = bool
  default     = true
}

# --- Database ----------------------------------------------------------------

variable "db_engine_version" {
  description = "PostgreSQL major.minor version. Verify against the current RDS-supported version list before changing (phase 12 section 13)."
  type        = string
  default     = "16.10"
}

variable "db_instance_class" {
  description = "RDS instance class. db.t4g.medium is a reasonable small-production starting point, not a load-tested recommendation — see phase 12 section 59-60."
  type        = string
  default     = "db.t4g.medium"
}

variable "db_allocated_storage_gb" {
  type    = number
  default = 100
}

variable "db_max_allocated_storage_gb" {
  description = "Ceiling for RDS storage autoscaling."
  type        = number
  default     = 500
}

variable "db_multi_az" {
  description = "Multi-AZ RDS (phase 12 section 13). Should be true for production; a business/cost decision for staging."
  type        = bool
  default     = false
}

variable "db_backup_retention_days" {
  type    = number
  default = 7
}

variable "db_deletion_protection" {
  type    = bool
  default = true
}

variable "db_name" {
  type    = string
  default = "easybook"
}

# --- Redis ---------------------------------------------------------------

variable "redis_node_type" {
  type    = string
  default = "cache.t4g.small"
}

variable "redis_multi_az" {
  description = "Multi-AZ ElastiCache replication group with automatic failover. Production should be true; a cost decision for staging."
  type        = bool
  default     = false
}

# --- ECS / containers ------------------------------------------------------

variable "container_image" {
  description = "Full ECR image URI (repository:tag or repository@digest) to deploy. No default — CI/CD supplies this per build, it must never silently fall back to :latest (phase 12 section 48-49)."
  type        = string
}

variable "ecs_services" {
  description = <<-EOT
    One entry per backend/Dockerfile-built ECS service (phase 12 section 11).
    `command` overrides the Dockerfile's default CMD; null keeps it (the "api"
    service). `desired_count` of 0 is valid for a service you want defined but
    not yet running. Celery Beat must stay at desired_count <= 1 — see its
    entry's comment and phase 12 section 11/18.
  EOT
  type = map(object({
    command          = optional(list(string))
    cpu              = number
    memory           = number
    desired_count    = number
    is_load_balanced = bool
  }))

  default = {
    api = {
      command          = null
      cpu              = 512
      memory           = 1024
      desired_count    = 2
      is_load_balanced = true
    }
    worker-critical = {
      command          = ["celery", "-A", "config", "worker", "--loglevel=info", "--queues=critical", "--concurrency=4"]
      cpu              = 512
      memory           = 1024
      desired_count    = 2
      is_load_balanced = false
    }
    worker-default = {
      command          = ["celery", "-A", "config", "worker", "--loglevel=info", "--queues=celery", "--concurrency=4"]
      cpu              = 512
      memory           = 1024
      desired_count    = 2
      is_load_balanced = false
    }
    worker-heavy = {
      command          = ["celery", "-A", "config", "worker", "--loglevel=info", "--queues=heavy", "--concurrency=2"]
      cpu              = 1024
      memory           = 2048
      desired_count    = 1
      is_load_balanced = false
    }
    worker-ai = {
      command          = ["celery", "-A", "config", "worker", "--loglevel=info", "--queues=ai", "--concurrency=2"]
      cpu              = 1024
      memory           = 2048
      desired_count    = 1
      is_load_balanced = false
    }
    beat = {
      # Exactly one replica, always — running two Beat processes double-fires
      # every schedule entry (phase 12 section 11/18; config/settings/base.py's
      # CELERY_BEAT_SCHEDULE has no distributed lock, by design: the tasks it
      # schedules are idempotent per-occurrence, not per-invocation-count).
      command          = ["celery", "-A", "config", "beat", "--loglevel=info"]
      cpu              = 256
      memory           = 512
      desired_count    = 1
      is_load_balanced = false
    }
  }

  validation {
    condition     = try(var.ecs_services["beat"].desired_count, 1) <= 1
    error_message = "The beat service must never run more than 1 replica (see its entry's comment)."
  }
}

# --- Domain / TLS / CDN ----------------------------------------------------

variable "domain_name" {
  description = "Public apex/API domain (e.g. api.easybook.example). Empty string defers Route53/ACM/CloudFront custom-domain resources until a real domain is decided — a business decision, not a default this file should invent (phase 12 section 9)."
  type        = string
  default     = ""
}

variable "acm_certificate_arn" {
  description = "ACM certificate ARN for the ALB/CloudFront listener, in the region CloudFront requires (us-east-1) for the CloudFront cert and this stack's own region for the ALB cert. No default — must be issued and validated out of band first."
  type        = string
  default     = ""
}

variable "frontend_origin_domain" {
  description = "Domain/URL of the deployed Next.js frontend, once phase 12 section 74's frontend work exists. Empty = CloudFront routes everything to the API only (frontend/ has no build yet — see root CLAUDE.md repo map)."
  type        = string
  default     = ""
}

# --- Alerting --------------------------------------------------------------

variable "alerts_email" {
  description = "Email address subscribed to the CloudWatch alarm SNS topic (phase 12 section 45). Empty = topic created with no subscriber; add one manually or via a follow-up apply once decided."
  type        = string
  default     = ""
}

# --- Tags --------------------------------------------------------------------

variable "extra_tags" {
  description = "Additional tags merged onto every resource (e.g. cost-center)."
  type        = map(string)
  default     = {}
}
