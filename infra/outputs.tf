output "load_balancer_dns_name" {
  description = "Point a CNAME (or Route 53 alias) for domain_name at this."
  value       = aws_lb.this.dns_name
}

output "ecr_repository_url" {
  value = aws_ecr_repository.app.repository_url
}

output "ecs_cluster_name" {
  value = aws_ecs_cluster.this.name
}

output "ecs_service_name" {
  value = aws_ecs_service.app.name
}

output "task_definition_family" {
  value = aws_ecs_task_definition.app.family
}

output "app_subnet_ids" {
  description = "For the one-off migration task (aws ecs run-task)."
  value       = aws_subnet.public[*].id
}

output "app_security_group_id" {
  value = aws_security_group.app.id
}

output "db_instance_identifier" {
  value = aws_db_instance.this.identifier
}

output "secret_key_secret_name" {
  description = "Put the SECRET_KEY value here once (see secrets.tf)."
  value       = aws_secretsmanager_secret.secret_key.name
}

output "github_deploy_role_arn" {
  description = "Store as the AWS_DEPLOY_ROLE_ARN variable of the GitHub 'production' environment."
  value       = aws_iam_role.github_deploy.arn
}

output "log_group_name" {
  value = aws_cloudwatch_log_group.app.name
}
