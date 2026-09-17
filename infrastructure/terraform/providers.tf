provider "aws" {
  region = var.aws_region

  default_tags {
    tags = merge(
      {
        Project     = var.project_name
        Environment = var.environment
        ManagedBy   = "terraform"
      },
      var.extra_tags,
    )
  }
}

locals {
  name = "${var.project_name}-${var.environment}"
}
