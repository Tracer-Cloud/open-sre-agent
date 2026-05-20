# tflint configuration for the bench Terraform module.
#
# Core tflint rules run by default. The AWS plugin adds AWS-specific checks
# (deprecated instance types, invalid IAM actions, etc.). Pin the plugin
# version so the rule set is reproducible across CI runs.

plugin "aws" {
  enabled = true
  version = "0.30.0"
  source  = "github.com/terraform-linters/tflint-ruleset-aws"
}

# Project-specific rule tweaks go here if/when findings need to be suppressed.
# Default: no suppressions — keep findings visible until we triage them.
