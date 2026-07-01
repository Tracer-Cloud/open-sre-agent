# Terraform state backend.
#
# Chicken-and-egg: the S3 bucket + DynamoDB lock table that hold this module's
# state cannot themselves be created by this module's S3 backend. Bootstrap
# happens in one of two ways:
#
#   1. First apply with the `backend "local"` block below (uncomment), then
#      manually create the state bucket + lock table, then comment local back
#      out, uncomment the `backend "s3"` block, and run `terraform init
#      -migrate-state`.
#
#   2. Or create the state bucket + lock table out-of-band (AWS CLI / console)
#      *before* the first apply, then start directly with the `backend "s3"`
#      block.
#
# State bucket must:
#   - Exist in the same region as var.region
#   - Have versioning enabled (recover from accidental state corruption)
#   - Have server-side encryption enabled
#   - Have public access blocked
#
# Lock table must:
#   - Have primary key `LockID` (String)
#   - Use PAY_PER_REQUEST billing (state operations are infrequent)

terraform {
  # Uncomment for first apply, then migrate to s3 below.
  # backend "local" {}

  backend "s3" {
    # Bucket name is account-disambiguated. S3 names are global; including
    # the account ID makes the bucket name unique across the world and
    # makes ownership obvious from the name alone.
    bucket         = "tracer-cloud-tfstate-395261708130"
    key            = "opensre-bench/terraform.tfstate"
    region         = "us-east-1"
    dynamodb_table = "tracer-cloud-tflock"
    encrypt        = true
  }
}
