# TradeLedger infrastructure (Terraform)

This folder describes the AWS environment for TradeLedger as code.

> **Status: never applied.**
> - **No AWS account was used to write or test it.** The Terraform binary was not available in the build environment, so even `terraform validate` has not run yet. The CI job `terraform` runs `fmt -check` and `validate` without any AWS credentials.
> - **What was checked:** `tests/test_deployment_files.py` checks the files as text (references resolve, the database is private and encrypted, IAM has no wildcard actions, no credentials).
> - **It costs money:** applying it creates resources that cost roughly USD 55–70 a month (see `docs/PHASE_8_COST_ESTIMATE.md`).

## What it creates

| File | Resources |
|---|---|
| `network.tf` | VPC, 2 public + 2 private subnets in two Availability Zones, internet gateway, route tables. **No NAT gateway** (saves about USD 33 a month). |
| `security_groups.tf` | Load balancer (443/80 from the internet) → app (8000 from the load balancer only) → database (5432 from the app only) |
| `alb.tf` | Application Load Balancer with HTTP→HTTPS redirect, TLS 1.2+, and a host-name rule (anything else gets 404) |
| `ecs.tf` | ECS cluster, task definition (Fargate, non-root, read-only filesystem, secrets from Secrets Manager), service with a deployment circuit breaker, log group |
| `rds.tf` | RDS PostgreSQL 16 (private, encrypted, TLS forced, 7-day backups, final snapshot, deletion protection, RDS-managed password) |
| `ecr.tf` | Private image repository (immutable tags, scan on push, keep the last 10 images) |
| `secrets.tf` | An empty secret for `SECRET_KEY`. You set the value yourself, so it never enters the Terraform state. |
| `iam.tf` | Execution role (pull the image, write logs, read two secrets); task role (no permissions) |
| `github_oidc.tf` | Deploy role for GitHub Actions through OIDC: only this repository's `production` environment, with only ECR/ECS/RDS-snapshot actions |
| `monitoring.tf` | SNS topic, alarms (5xx errors, unhealthy tasks, database CPU, free storage) and saved Logs Insights queries |

## Before you start

1. An AWS account you control, with billing alerts set (AWS Budgets) and an IAM Identity Center or other short-lived login. **Never use root access keys.**
2. Terraform 1.10 or newer, and the AWS CLI v2.
3. A domain name and a public ACM certificate for it in the same region (`aws acm request-certificate --domain-name journal.example.com --validation-method DNS`, then add the DNS record).
4. An S3 bucket for the state, in the same region:
   - private, with "Block Public Access" on;
   - versioning on;
   - default encryption on;
   - readable only by the people who run Terraform.

## Commands (each `apply` can incur charges)

```bash
cd infra
cp terraform.tfvars.example production.tfvars        # fill in; ignored by Git
terraform init -backend-config="bucket=YOUR-STATE-BUCKET" \
               -backend-config="key=tradeledger/production.tfstate" \
               -backend-config="region=eu-west-2"
terraform fmt -check && terraform validate
terraform plan -var-file=production.tfvars -out=tfplan   # read every line before applying
terraform apply tfplan                                    # CREATES CHARGEABLE RESOURCES
```

After the first apply:

```bash
# 1. The Flask secret key (once):
aws secretsmanager put-secret-value --secret-id "$(terraform output -raw secret_key_secret_name)" \
  --secret-string "$(python -c 'import secrets; print(secrets.token_hex(32))')"
# 2. Point your DNS name (CNAME) at: terraform output -raw load_balancer_dns_name
# 3. Push the first image and deploy: see docs/PHASE_8_AWS_DEPLOYMENT.md
```

## Stopping and destroying (to stop the bills)

- **Pause compute only:** `terraform apply -var-file=production.tfvars -var desired_count=0`. Fargate charges and the task's public IP stop. **The load balancer, its IP addresses, the database and its storage keep costing money.**
- **Pause the database:**
  - `aws rds stop-db-instance --db-instance-identifier tradeledger-production`;
  - storage is still charged;
  - **AWS restarts a stopped instance automatically after 7 days.**
- **Remove everything:**
  1. Set `deletion_protection = false` and apply.
  2. Run `terraform destroy -var-file=production.tfvars`.
  3. **Even then** some charges remain:
     - the final database snapshot and any manual snapshots;
     - images left in ECR (the repository refuses deletion while images exist; delete them first);
     - CloudWatch log groups created outside Terraform;
     - the state bucket;
     - Secrets Manager secrets during their 7-day recovery window.
  4. Delete the leftovers you no longer need in the console.
