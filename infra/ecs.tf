# ECS on Fargate: AWS runs the containers; there are no servers to patch.

resource "aws_cloudwatch_log_group" "app" {
  name              = "/ecs/${local.name}"
  retention_in_days = var.log_retention_days
}

resource "aws_ecs_cluster" "this" {
  name = local.name

  setting {
    name  = "containerInsights"
    value = "disabled" # extra CloudWatch cost; enable while investigating performance
  }
}

locals {
  db_secret_arn = aws_db_instance.this.master_user_secret[0].secret_arn

  container = {
    name                   = "app"
    image                  = "${aws_ecr_repository.app.repository_url}:${var.image_tag}"
    essential              = true
    readonlyRootFilesystem = true
    user                   = "10001:10001"
    portMappings           = [{ containerPort = 8000, protocol = "tcp" }]

    environment = [
      { name = "TRADELEDGER_CONFIG", value = "production" },
      { name = "DB_HOST", value = aws_db_instance.this.address },
      { name = "DB_PORT", value = tostring(aws_db_instance.this.port) },
      { name = "DB_NAME", value = "tradeledger" },
      { name = "DB_SSLMODE", value = "require" },
      # One proxy (the ALB) sets X-Forwarded-For/-Proto.
      { name = "TRADELEDGER_PROXY_HOPS", value = "1" },
      # The Host header is enforced by the load balancer's listener rule
      # (alb.tf), which only forwards requests for var.domain_name. The
      # app-level check stays off here because ALB health checks use the
      # task's IP address as the Host.
      { name = "TRADELEDGER_REGISTRATION_ENABLED", value = "0" },
      { name = "WEB_CONCURRENCY", value = "2" },
    ]

    # Injected by ECS at start-up from Secrets Manager; never in the image,
    # the task definition text or the logs.
    secrets = [
      { name = "SECRET_KEY", valueFrom = aws_secretsmanager_secret.secret_key.arn },
      { name = "DB_USER", valueFrom = "${local.db_secret_arn}:username::" },
      { name = "DB_PASSWORD", valueFrom = "${local.db_secret_arn}:password::" },
    ]

    mountPoints = [{ sourceVolume = "tmp", containerPath = "/tmp", readOnly = false }]

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.app.name
        awslogs-region        = var.region
        awslogs-stream-prefix = "app"
      }
    }
  }
}

resource "aws_ecs_task_definition" "app" {
  family                   = local.name
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.task_cpu
  memory                   = var.task_memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = var.cpu_architecture
  }

  # Writable scratch space for Gunicorn; the root filesystem is read-only.
  volume {
    name = "tmp"
  }

  container_definitions = jsonencode([local.container])
}

resource "aws_ecs_service" "app" {
  name                              = local.name
  cluster                           = aws_ecs_cluster.this.id
  task_definition                   = aws_ecs_task_definition.app.arn
  desired_count                     = var.desired_count
  launch_type                       = "FARGATE"
  health_check_grace_period_seconds = 60
  enable_execute_command            = false
  propagate_tags                    = "SERVICE"

  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200

  # A deployment whose tasks never become healthy is rolled back automatically.
  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  network_configuration {
    subnets          = aws_subnet.public[*].id
    security_groups  = [aws_security_group.app.id]
    assign_public_ip = true # see network.tf: needed without a NAT gateway
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.app.arn
    container_name   = "app"
    container_port   = 8000
  }

  lifecycle {
    # The deploy workflow registers new task definitions; Terraform must not
    # roll them back on the next plan.
    ignore_changes = [task_definition]
  }

  depends_on = [aws_lb_listener.https]
}
