# Phase 8 AWS deployment guide

> **Status: designed and written as code, NOT deployed.**
> - **No AWS account was used.** No resources exist, no `terraform apply` has run, and the deploy workflow has never run.
> - **Every command marked 💷 can create charges.** Read [`PHASE_8_COST_ESTIMATE.md`](PHASE_8_COST_ESTIMATE.md) first.

## 1. The architecture, in plain English

- A **load balancer** is the only thing on the internet. It holds the HTTPS certificate and only passes on requests for your domain name.
- Behind it, AWS **Fargate** runs one or more copies of the TradeLedger Docker image. There are no servers for you to patch.
- The containers talk to an **RDS PostgreSQL** database that sits in private subnets with no internet route. Only the containers' security group may connect, and only over TLS.
- **Secrets Manager** holds the Flask secret key and the database password. ECS hands them to the container when it starts, so they are never in the image or in Git.
- **CloudWatch** keeps the JSON logs and raises alarms.
- **GitHub Actions** deploys new versions, but only when a person starts it and a reviewer approves it, using short-lived OIDC credentials instead of stored keys.

The components are listed in [`infra/README.md`](../infra/README.md) and [`TRADELEDGER_REVIEW_HANDOVER.md`](TRADELEDGER_REVIEW_HANDOVER.md).

## 2. Why this design

| Decision | Reason | Alternative and trade-off |
|---|---|---|
| ECS Fargate | Managed containers, per-second billing, no operating system to patch, widely used in industry | One EC2 host with Compose: cheaper, but you patch it. **App Runner closed to new customers on 30 April 2026.** ECS Express Mode is simpler but hides the parts worth learning |
| RDS PostgreSQL | Managed backups, point-in-time recovery, patching, encryption, automatic password management | PostgreSQL in a container: cheaper, but backups and durability become your job |
| Application Load Balancer | HTTPS termination with a free ACM certificate, health checks, host-name rule, rolling deployments | NLB plus TLS in Gunicorn: more work, less protection |
| No NAT gateway | Saves about USD 33 a month | Tasks have public IPs, but their security group accepts only the load balancer. Stricter: private subnets with NAT or VPC endpoints |
| Secrets Manager (not environment values in Terraform) | Values never enter Git, images, task definition text or Terraform state | SSM Parameter Store SecureString: cheaper, slightly fewer features |
| GitHub OIDC | No long-lived AWS keys exist to leak | Access keys in GitHub secrets: simpler, much riskier |

## 3. Security controls

- **Network:** only ports 443/80 are open to the internet, on the load balancer. The app accepts port 8000 from the load balancer only. The database accepts 5432 from the app only, has no public address and no route to the internet.
- **HTTPS:** HTTP is redirected to HTTPS; the TLS policy is TLS 1.2 or newer. The app sends HSTS and marks its cookie Secure with the `__Host-` prefix. RDS refuses unencrypted connections (`rds.force_ssl=1`), and the app connects with `DB_SSLMODE=require`.
- **Host names:** the load balancer only forwards requests whose Host is your domain; everything else gets 404. That covers scanners and DNS-rebinding attempts.
  - This is why the app's own Host check is off on AWS: ALB health checks use the task's IP address as the Host.
- **Proxy headers:** `TRADELEDGER_PROXY_HOPS=1`, so the app trusts exactly one `X-Forwarded-For` and `X-Forwarded-Proto` hop (the load balancer) and never `X-Forwarded-Host`.
- **Containers:** non-root user 10001, read-only root filesystem with a scratch `/tmp` volume, no SSH or ECS Exec, Gunicorn rather than Flask's development server, and debug mode refused.
- **IAM (least privilege):**
  - **Execution role:** can pull the image, write logs and read exactly two secrets.
  - **Task role:** no permissions at all.
  - **Deploy role:** limited to:
    - pushing to this repository's ECR repository;
    - registering task definitions and updating or running tasks on this one cluster and service;
    - passing only the two app roles to ECS;
    - creating database snapshots.

    The only wildcard resources are the ones AWS requires (`ecr:GetAuthorizationToken` and `ecs:RegisterTaskDefinition` have no resource-level permissions).
- **Database:**
  - Encrypted at rest.
  - The master password is generated and rotated by RDS, never seen by Terraform.
  - 7-day automated backups, deletion protection and a final snapshot.
  - **Known gap:** the app uses the master user. A dedicated application role is safer: it would have only the privileges it needs on the TradeLedger tables (see the security review, finding P8-11).
  - **Since Phase 8.5:** `flask --app tradeledger grant-app-role --role <name>` grants such a role row access only. It is tested on PostgreSQL 16. Wiring it into AWS needs a second secret, a migration task that runs as the owner, and the service connecting as the app role. None of this is done yet (`docs/TRADELEDGER_FINAL_SECURITY_REVIEW.md` §6).
- **Terraform state:** an encrypted, versioned, private S3 bucket with native locking. No secret values are written to state by design.

## 4. Before the first deployment

1. An AWS account with billing alerts (AWS Budgets) and a short-lived administrator login (IAM Identity Center). **Never root keys.**
2. A domain and a **public ACM certificate** for it in eu-west-2 (DNS validation).
3. A private, versioned, encrypted S3 bucket for Terraform state.
4. A GitHub repository with an environment called **`production`** that has **required reviewers**.

## 5. Step by step

```bash
# 💷 1. Infrastructure
cd infra
cp terraform.tfvars.example production.tfvars    # domain, certificate ARN, github_repository
terraform init -backend-config="bucket=YOUR-STATE-BUCKET" -backend-config="key=tradeledger/production.tfstate" -backend-config="region=eu-west-2"
terraform plan -var-file=production.tfvars -out=tfplan      # read it
terraform apply tfplan

# 2. The Flask secret key (once; never stored in Terraform)
aws secretsmanager put-secret-value --secret-id "$(terraform output -raw secret_key_secret_name)" \
  --secret-string "$(python -c 'import secrets; print(secrets.token_hex(32))')"

# 3. DNS: a CNAME from your domain to the load balancer
terraform output -raw load_balancer_dns_name
```

**4. GitHub configuration.** Set these as **variables** (not secrets) on the `production` environment, with the values from `terraform output`:
- `AWS_REGION` (eu-west-2) and `AWS_DEPLOY_ROLE_ARN`;
- `ECR_REPOSITORY_URL`, `ECS_CLUSTER`, `ECS_SERVICE` and `TASK_FAMILY`;
- `APP_SUBNETS` (comma-separated subnet ids) and `APP_SECURITY_GROUP`;
- `DB_INSTANCE_ID`.

Then set the repository variable `DEPLOY_ENABLED=true`. No AWS keys are stored anywhere.

**💷 5. Deploy:** Actions → "Deploy (manual)" → Run workflow → a reviewer approves. The workflow then:
1. builds and pushes the image, tagged with the commit SHA;
2. snapshots the database;
3. registers a task definition;
4. runs `flask --app tradeledger migrate-db --backed-up` as a one-off task (the first run creates the schema);
5. updates the service and waits until it is stable.

**6. First account** (self-registration is off in production). Run a one-off task:

```bash
aws ecs run-task --cluster tradeledger-production --launch-type FARGATE \
  --task-definition tradeledger-production \
  --network-configuration "awsvpcConfiguration={subnets=[subnet-...],securityGroups=[sg-...],assignPublicIp=ENABLED}" \
  --overrides '{"containerOverrides":[{"name":"app","command":["sh","-c","printf %s \"$TL_FIRST_PASSWORD\" | flask --app tradeledger create-user --email you@example.com --password-stdin"],"environment":[{"name":"TL_FIRST_PASSWORD","value":"..."}]}]}'
```

The override above puts the password into the ECS task's recorded overrides, so it must be changed straight after the first sign-in (Account → Change password). A cleaner approach is a one-off secret in Secrets Manager referenced by the task.

**7. Importing a local journal (optional).** Run `scripts/migrate_sqlite_to_postgres.py` from a machine that can reach the database. The database is private, so that means a temporary bastion or an ECS task, which is out of scope here. The simpler option for a portfolio is to start fresh.

## 6. Monitoring and troubleshooting

- **Logs:** CloudWatch log group `/ecs/tradeledger-production`, one JSON object per line. The saved Logs Insights queries (`monitoring.tf`) list server errors and failed sign-ins.
  - Every response carries `X-Request-ID`; search the logs for that id to see everything one request did.
- **Health:**
  - `/health/live` answers when the process runs (the Docker healthcheck).
  - `/health/ready` also checks the database (the load balancer target group). An unready task receives no traffic. The reason is logged; the response only says "unavailable".
- **Alarms:** 5 or more 5xx responses in 5 minutes; an unhealthy task for 3 minutes; database CPU above 80% for 15 minutes; under 2 GB of database storage left. Add your email with `alert_email` and confirm the subscription.

| Symptom | Likely cause | Check |
|---|---|---|
| Task stops at once, "unable to pull secrets" | SECRET_KEY secret has no value yet, or the execution role cannot read it | Step 2 above; the task's "stopped reason" |
| Task stops, log says "SECRET_KEY is too weak" | The secret is short or a placeholder | Put a 64-hex-character value |
| Targets unhealthy, 503 from `/health/ready` | Database unreachable: security group, TLS, or the database is stopped | Log line `health.ready.failed`; RDS status |
| Every page 404 "Not found" (plain text) | The request's Host is not `domain_name` | DNS record and the listener rule |
| Users all appear to come from one IP; sign-in limits trigger too easily | `TRADELEDGER_PROXY_HOPS` missing | Task definition environment |
| 502 errors now and then | Gunicorn closing connections before the load balancer | `keepalive` (75 s) must exceed the load balancer idle timeout (60 s) |

## 7. Stopping and deleting

See [`infra/README.md`](../infra/README.md#stopping-and-destroying-to-stop-the-bills). In short:
- `desired_count = 0` stops the compute charges only.
- The load balancer, IP addresses, database and storage keep charging until `terraform destroy` (after turning off deletion protection).
- Snapshots, ECR images, log groups and the state bucket can survive a destroy and keep costing small amounts.

## 8. What is not covered

- **Not done:**
  - an actual deployment;
  - running the workflow;
  - an RDS restore drill;
  - load testing on AWS;
  - a WAF;
  - multi-AZ, autoscaling or a staging environment;
  - a dedicated database role for the app.
- **Each would be a reasonable next step,** in this order:
  1. deploy and record evidence;
  2. a staging environment;
  3. a dedicated database role;
  4. WAF rate limiting.
