# S3 / document storage outage

## Blast radius

`documents/` app only (upload, download, OCR trigger) plus anything that
generates a signed URL for a document (`DOCUMENT_SIGNED_URL_TTL_SECONDS`).
Accounting/reports/tax do not read from S3 directly — a `DocumentLink` is
metadata in Postgres regardless of whether the underlying object is
reachable, so an S3 outage does not corrupt or block any financial workflow,
only document upload/download/preview itself.

## Symptoms

- Upload/download endpoints under `/api/v1/documents/` erroring or timing out.
- `documents.tasks.run_ocr_task` failing (Celery task, so also check
  [redis-celery-outage.md](redis-celery-outage.md) if the underlying cause is
  actually Redis/Celery rather than S3 itself).

## Actions

1. Check AWS's S3 service health dashboard for the region in
   `var.aws_region` — a genuine multi-region S3 outage is rare; more likely
   causes are the bucket's own IAM policy, the `aws_iam_role.ecs_task`
   permissions (`terraform/iam.tf`'s `ecs_task_s3_documents` policy), or
   `DOCUMENT_STORAGE_S3_BUCKET`/`_REGION` misconfiguration after a deploy.
2. Confirm `aws_s3_bucket_public_access_block.documents` and the bucket
   policy haven't been manually changed outside Terraform — `terraform plan`
   should show zero drift; if it doesn't, someone hand-edited the bucket
   (phase 12 section 72's "no casual bypass of IaC").
3. There is nothing to "fail over to" — a single S3 bucket has no
   application-level fallback storage backend configured
   (`DOCUMENT_STORAGE_BACKEND=local` is dev/test-only and is actively
   rejected in production by `config/settings/preflight.py`). This is a wait-
   for-AWS-recovery scenario, not something to route around.

## Data safety during the outage

Versioning (`aws_s3_bucket_versioning.documents`) means an outage itself
cannot lose data that was already successfully written — only new
uploads/reads during the outage window are affected, not existing objects.
