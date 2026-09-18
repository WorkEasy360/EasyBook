# Remote state with locking is required, not optional. With local state, two
# people (or a person and CI) applying at once corrupt or silently overwrite
# each other's view of production, and the only copy of what Terraform manages
# lives on one laptop.
#
# The state bucket is created once by bootstrap/ (it cannot hold the state of
# the run that creates it). Locking uses S3-native lock files (use_lockfile,
# generally available since Terraform 1.11) — the DynamoDB lock table arguments
# are deprecated — hence the required_version floor below.
#
# Partial configuration: bucket, key and region are per environment and come
# from a backend config file at init time, never hard-coded here —
#   terraform init -backend-config=backend/staging.s3.tfbackend
# (copy backend/example.s3.tfbackend). One state file per environment, never
# shared (variables.tf, var.environment). See README.md.

terraform {
  required_version = ">= 1.11.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  backend "s3" {
    use_lockfile = true
    encrypt      = true
  }
}
