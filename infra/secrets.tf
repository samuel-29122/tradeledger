# The Flask SECRET_KEY lives in Secrets Manager. Terraform creates the empty
# secret; you put the value in ONCE, from your own machine, so it never
# appears in Terraform files or state:
#
#   aws secretsmanager put-secret-value \
#     --secret-id tradeledger-production/secret-key \
#     --secret-string "$(python -c 'import secrets; print(secrets.token_hex(32))')"
#
# The database credentials are the RDS-managed secret (rds.tf).

resource "aws_secretsmanager_secret" "secret_key" {
  name                    = "${local.name}/secret-key"
  description             = "Flask SECRET_KEY for TradeLedger (signs session cookies)"
  recovery_window_in_days = 7
}
