# Bench container image registry.
#
# Image is built and pushed by CI (or developer) to this repo; Fargate task
# definition references it by digest (not :latest) so the pre-registration
# pins exactly which image ran.

resource "aws_ecr_repository" "bench" {
  name                 = local.name_prefix
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "AES256"
  }
}

# Expire ONLY untagged image layers (orphan blobs left over when a tag is
# overwritten or deleted). Tagged images live forever because the
# pre-registration pins a specific tag/digest for reproducibility — a
# lifecycle that expires tagged images would silently break a future
# re-run of a published bench. ECR storage for tagged images is
# negligible (~$0.10/GB/month); we will not push thousands.
resource "aws_ecr_lifecycle_policy" "bench" {
  repository = aws_ecr_repository.bench.name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Expire untagged image layers older than 14 days"
        selection = {
          tagStatus   = "untagged"
          countType   = "sinceImagePushed"
          countUnit   = "days"
          countNumber = 14
        }
        action = { type = "expire" }
      }
    ]
  })
}
