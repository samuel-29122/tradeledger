# Phase 8 cost estimate (AWS, London region)

> **These are estimates, not quotes.**
> - **Status:** nothing has been deployed, so there is no real bill to compare against.
> - **Source of the prices:** web sources found on 15 November 2026, not the AWS Price List API. Where a London-specific price could not be confirmed, the table says so and uses the nearest published figure.
> - **Before you deploy:** check every line in the [AWS Pricing Calculator](https://calculator.aws/).
> - **Not assumed:** this estimate does **not** assume that any AWS Free Tier applies to your account. Whether it does depends on your account's age and plan.

## The design being costed

- **Compute:** one ECS Fargate task (0.25 vCPU, 0.5 GB, Linux x86) running 24 hours a day.
- **Database:** RDS PostgreSQL `db.t4g.micro`, one Availability Zone, 20 GB gp3 storage, 7 days of backups.
- **Load balancer:** one Application Load Balancer in two zones.
- **Other:** Secrets Manager, CloudWatch Logs and four alarms, and ECR.
- **No NAT gateway.**
- **Region:** eu-west-2 (London). A month is taken as 730 hours.

## Monthly estimate

| Item | Basis | Estimate (USD/month) | Confidence |
|---|---|---|---|
| ECS Fargate (1 task, 0.25 vCPU + 0.5 GB) | about $0.0456 per vCPU-hour and $0.0050 per GB-hour (published eu-west-1 rates; London is usually slightly higher) | **$10–11** | Medium: London rate not confirmed |
| RDS db.t4g.micro, single-AZ | about $0.018/hour in eu-west-2 (Spare Cores, Bytebase) | **$13** | Medium–high |
| RDS gp3 storage, 20 GB | roughly $0.13 per GB-month | **$2.5–3** | Low: London gp3 price not confirmed |
| RDS backups | free up to the database's size; more if snapshots pile up | **$0–1** | Medium |
| Application Load Balancer, hours | about $0.0225–0.027/hour (US East $0.0225; London typically higher) | **$16–20** | Medium |
| Load balancer capacity units (LCU) | a low-traffic site uses a small fraction of one LCU, at about $0.008/hour each | **$1–6** | Low: depends on traffic |
| Public IPv4 addresses | $0.005 per address per hour (charged since February 2024): 2 for the load balancer + 1 for the task | **$11** | High (AWS announcement) |
| Secrets Manager | $0.40 per secret: SECRET_KEY + the RDS-managed secret | **$0.80** | High |
| CloudWatch | small log volume, 4 alarms at about $0.10 | **$1–2** | Medium |
| ECR storage | a few hundred MB of images | **< $0.10** | High |
| Data transfer out | low traffic is within AWS's monthly free data-transfer allowance | **$0** | Medium |
| **Total, running 24/7** | | **about $55–70** | |

**Optional extras:**
- Route 53 hosted zone: $0.50 a month.
- A domain: about $10–15 a year.
- Multi-AZ database: roughly doubles the RDS line.
- AWS WAF: $5 a month plus about $1 per rule.
- A NAT gateway: about $33 a month plus data.

## The biggest cost drivers, and how to cut them

1. **The load balancer and public IPv4 addresses (about $28–37 together).** They cost money as long as they exist, with or without traffic. They cannot be "stopped": only `terraform destroy` removes them.
2. **The database (about $15).** It can be stopped for up to 7 days at a time; AWS then restarts it automatically. You still pay for storage while it is stopped.
3. **Fargate (about $10).** Setting `desired_count = 0` stops this line and the task's IP charge.

**For a portfolio project, the cheapest honest approach** is to deploy, record a demonstration and screenshots, then destroy everything (see `infra/README.md`). Even a full month is cheaper than the effort of keeping it running.

## Alternatives considered

| Option | Rough monthly cost | Why it was not chosen |
|---|---|---|
| **AWS App Runner** | — | Closed to new customers from 30 April 2026 (AWS announcement); maintenance mode only. |
| **ECS Express Mode** (AWS's suggested App Runner replacement) | similar to Fargate + load balancer | Newer and simpler, but it hides the load balancer, networking and IAM, which are exactly the parts this project wants to demonstrate. Worth evaluating in a later phase. |
| **One EC2 instance running Docker Compose** (e.g. t4g.small) | about $15–20 | Cheapest. But you patch the operating system and the database yourself, and there is no managed backup or failover. |
| **Lightsail container + managed database** | about $25–35 | Simpler billing, but less transferable to AWS jobs. |

## Before applying, set a budget

```bash
aws budgets create-budget --account-id YOUR_ACCOUNT_ID --budget \
  '{"BudgetName":"tradeledger","BudgetLimit":{"Amount":"40","Unit":"USD"},"TimeUnit":"MONTHLY","BudgetType":"COST"}' \
  --notifications-with-subscribers \
  '[{"Notification":{"NotificationType":"ACTUAL","ComparisonOperator":"GREATER_THAN","Threshold":80},"Subscribers":[{"SubscriptionType":"EMAIL","Address":"you@example.com"}]}]'
```

(A budget **alerts** you; it does not stop resources.)

## Sources

- Fargate rates (published US East and EU rates): Vantage and Cloudchipr pricing guides.
- RDS `db.t4g.micro` in eu-west-2: Spare Cores and Bytebase.
- Public IPv4 charge: the AWS News Blog, "AWS Public IPv4 Address Charge + Public IP Insights".
- App Runner availability change: the AWS App Runner documentation and the InfoQ coverage of April 2026.
- Load balancer and NAT figures: AWS pricing summaries quoted by usage.ai (May 2026) and the Elastic Load Balancing pricing page.
