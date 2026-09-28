# Documents remain private (phase 12 section 17): no public bucket, no
# public ACLs, encrypted at rest, versioned so an accidental overwrite/delete
# is recoverable. backend/documents' signed-URL flow (DOCUMENT_SIGNED_URL_TTL_SECONDS)
# is the only way anything outside AWS ever reads an object here.

resource "aws_s3_bucket" "documents" {
  bucket = "${local.name}-documents"
}

resource "aws_s3_bucket_public_access_block" "documents" {
  bucket = aws_s3_bucket.documents.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "documents" {
  bucket = aws_s3_bucket.documents.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "documents" {
  bucket = aws_s3_bucket.documents.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "aws:kms"
    }
    bucket_key_enabled = true
  }
}

# Old versions of a superseded/deleted document don't need to live forever —
# noncurrent versions expire after 1 year; current versions are governed by
# the application's own retention rules (phase 12 section 65), not by S3.
resource "aws_s3_bucket_lifecycle_configuration" "documents" {
  bucket = aws_s3_bucket.documents.id

  rule {
    id     = "expire-noncurrent-versions"
    status = "Enabled"

    noncurrent_version_expiration {
      noncurrent_days = 365
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}
