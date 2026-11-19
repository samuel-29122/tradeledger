# Remote state in S3 (configure once per environment, see infra/README.md):
#
#   terraform init \
#     -backend-config="bucket=YOUR-STATE-BUCKET" \
#     -backend-config="key=tradeledger/production.tfstate" \
#     -backend-config="region=eu-west-2"
#
# Why remote state matters: the state file records every resource and can
# contain sensitive values (for example database endpoints and ARNs). It
# must never be committed (see .gitignore) and belongs in a private,
# versioned, encrypted bucket with public access blocked, readable only by
# the people and roles that run Terraform. use_lockfile = true makes S3
# itself lock the state, so two people cannot apply at once.
#
# This configuration deliberately writes NO secret values into state: the
# database password is generated and kept by RDS (manage_master_user_password)
# and the Flask SECRET_KEY is put into Secrets Manager by hand (README).

terraform {
  backend "s3" {
    encrypt      = true
    use_lockfile = true
  }
}
