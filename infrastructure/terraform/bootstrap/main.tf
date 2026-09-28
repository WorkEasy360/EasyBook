# One-time bootstrap for the main stack's remote state (../versions.tf).
#
# The S3 bucket that holds Terraform state cannot be created by the run whose
# state it stores, so this tiny root module creates it first, with local
# state. Locking needs no DynamoDB table: the main stack's S3 backend uses
# S3-native lock files (use_lockfile, Terraform >= 1.11); the DynamoDB lock
# arguments are deprecated.
#
# Run once per AWS account, then keep this module's own terraform.tfstate out
# of git (it is .gitignored) — it only describes this one bucket, and the
# bucket's prevent_destroy guard is what actually protects the state.

terraform {
  required_version = ">= 1.11.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

variable "aws_region" {
  description = "Region for the state bucket; use the same region as the main stack."
  type        = string
  default     = "ap-south-1"
}

variable "state_bucket_name" {
  description = "Globally unique bucket name, e.g. easybook-terraform-state-<account-id>. No default: S3 bucket names are global, so any default would collide."
  type        = string
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project   = "easybook"
      ManagedBy = "terraform"
      Purpose   = "terraform-state"
    }
  }
}

resource "aws_s3_bucket" "state" {
  bucket = var.state_bucket_name

  # Losing this bucket loses the record of every resource Terraform manages.
  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "aws:kms"
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "state" {
  bucket = aws_s3_bucket.state.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "state" {
  bucket = aws_s3_bucket.state.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

data "aws_iam_policy_document" "state_tls_only" {
  statement {
    sid     = "DenyInsecureTransport"
    effect  = "Deny"
    actions = ["s3:*"]
    resources = [
      aws_s3_bucket.state.arn,
      "${aws_s3_bucket.state.arn}/*",
    ]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "state" {
  bucket = aws_s3_bucket.state.id
  policy = data.aws_iam_policy_document.state_tls_only.json

  depends_on = [aws_s3_bucket_public_access_block.state]
}

# Old state versions are the rollback path for a bad apply; keep them a while,
# not forever.
resource "aws_s3_bucket_lifecycle_configuration" "state" {
  bucket = aws_s3_bucket.state.id

  rule {
    id     = "expire-noncurrent-state-versions"
    status = "Enabled"

    filter {}

    noncurrent_version_expiration {
      noncurrent_days = 90
    }
  }
}

output "state_bucket_name" {
  value = aws_s3_bucket.state.bucket
}
