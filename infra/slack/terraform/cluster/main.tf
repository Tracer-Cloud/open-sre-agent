terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.40"
    }
  }
}

provider "aws" {
  region = var.region
}

# One shared ECS cluster for all OpenSRE teams. Each team applies the per-team
# stack in ../ against this cluster (looked up by name), giving one silo ECS
# service per team on shared compute. ECS clusters are free; the isolation
# boundary is the per-team task role + security group, not the cluster.
resource "aws_ecs_cluster" "shared" {
  name = var.cluster_name

  setting {
    name  = "containerInsights"
    value = var.container_insights
  }

  tags = {
    component  = "opensre-shared-cluster"
    managed_by = "terraform"
  }
}
