# TradeLedger AWS infrastructure (Phase 8) - Terraform
#
# NOTHING here has been applied. `terraform fmt -check` and
# `terraform validate` run in CI (job "infrastructure"); no AWS account was
# used while writing it. Applying it creates CHARGEABLE resources: read
# docs/PHASE_8_COST_ESTIMATE.md and docs/PHASE_8_AWS_DEPLOYMENT.md first.

terraform {
  required_version = ">= 1.10.0" # S3 native state locking (use_lockfile)

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region = var.region

  # Credentials come from the environment (aws sso login, a named profile,
  # or GitHub OIDC). NEVER put access keys in these files.

  default_tags {
    tags = {
      Project     = "tradeledger"
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}
