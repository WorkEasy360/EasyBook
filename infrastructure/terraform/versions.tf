# Remote state is deliberately NOT configured here: an S3 backend bucket +
# DynamoDB lock table cannot store the state of the Terraform run that would
# create them (bootstrap chicken-and-egg). Create those two resources by hand
# (or via a separate one-time `terraform apply` with a local backend, then
# `terraform state mv`) before enabling the commented `backend "s3"` block
# below — see infrastructure/terraform/README.md.

terraform {
  required_version = ">= 1.9.0"

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

  # backend "s3" {
  #   bucket         = "easybook-terraform-state-<account-id>"
  #   key            = "easybook/terraform.tfstate"
  #   region         = "ap-south-1"
  #   dynamodb_table = "easybook-terraform-locks"
  #   encrypt        = true
  # }
}
