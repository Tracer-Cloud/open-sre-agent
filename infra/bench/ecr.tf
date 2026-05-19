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

# Retain the 20 most-recent images; delete older untagged blobs to control
# storage cost. Pinned production images stay in repo because they keep their
# tag.
resource "aws_ecr_lifecycle_policy" "bench" {
  repository = aws_ecr_repository.bench.name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Retain 20 most recent images"
        selection = {
          tagStatus   = "any"
          countType   = "imageCountMoreThan"
          countNumber = 20
        }
        action = { type = "expire" }
      }
    ]
  })
}
