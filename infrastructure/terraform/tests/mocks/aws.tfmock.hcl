# Deterministic stand-ins for values AWS would compute, so `terraform test`
# can plan this stack with no credentials (see infrastructure-ci.yml).
mock_data "aws_availability_zones" {
  defaults = {
    names = ["ap-south-1a", "ap-south-1b", "ap-south-1c"]
  }
}

mock_data "aws_iam_policy_document" {
  defaults = {
    json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}"
  }
}

mock_resource "aws_db_instance" {
  defaults = {
    master_user_secret = [{
      secret_arn    = "arn:aws:secretsmanager:ap-south-1:123456789012:secret:rds-master"
      kms_key_id    = ""
      secret_status = "active"
    }]
  }
}

mock_data "aws_caller_identity" {
  defaults = {
    account_id = "123456789012"
  }
}
