locals {
  common_tags = {
    project     = var.project
    component   = "benchmark"
    managed-by  = "terraform"
    cost-center = "benchmark"
    repository  = var.github_repository
  }

  name_prefix = var.project
}
