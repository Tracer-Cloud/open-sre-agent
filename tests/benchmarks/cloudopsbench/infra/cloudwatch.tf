# CloudWatch log group for the Fargate task.
#
# Bench artifacts of record live in S3. CloudWatch is for live tail during
# the run + post-run investigation of failed cells.
#
# If/when the team wants logs in the Grafana UI, set up Grafana Cloud's
# native CloudWatch data source (configured in Grafana Cloud's web UI, not
# here). Logs stay in CloudWatch; Grafana queries them. Zero AWS infra
# change required.

resource "aws_cloudwatch_log_group" "bench" {
  name              = "/ecs/${local.name_prefix}"
  retention_in_days = var.log_retention_days
}
