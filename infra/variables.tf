variable "region" {
  description = "AWS region. eu-west-2 is London."
  type        = string
  default     = "eu-west-2"
}

variable "environment" {
  description = "Name used in resource names and tags."
  type        = string
  default     = "production"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,20}$", var.environment))
    error_message = "Use lower-case letters, digits and hyphens (2-21 characters)."
  }
}

variable "domain_name" {
  description = "Host name users type, e.g. journal.example.com. The load balancer only forwards requests for this host."
  type        = string
}

variable "certificate_arn" {
  description = "ARN of an ACM certificate for domain_name in the same region (DNS-validated; ACM public certificates are free)."
  type        = string
}

variable "vpc_cidr" {
  description = "Address range of the new VPC."
  type        = string
  default     = "10.20.0.0/16"
}

variable "image_tag" {
  description = "Image tag to run. CI deployments replace the task definition; this is only the first one."
  type        = string
  default     = "initial"
}

variable "desired_count" {
  description = "Number of running app tasks. Set 0 to stop compute charges (the load balancer and database still cost money)."
  type        = number
  default     = 1
}

variable "task_cpu" {
  description = "Fargate CPU units (256 = 0.25 vCPU)."
  type        = number
  default     = 256
}

variable "task_memory" {
  description = "Fargate memory in MiB (512 is the minimum for 0.25 vCPU)."
  type        = number
  default     = 512
}

variable "cpu_architecture" {
  description = "X86_64 matches the image CI builds. ARM64 (Graviton) is about 20% cheaper but needs an arm64 image."
  type        = string
  default     = "X86_64"

  validation {
    condition     = contains(["X86_64", "ARM64"], var.cpu_architecture)
    error_message = "Use X86_64 or ARM64."
  }
}

variable "db_instance_class" {
  description = "RDS instance size. db.t4g.micro is the smallest current PostgreSQL class."
  type        = string
  default     = "db.t4g.micro"
}

variable "db_allocated_storage_gb" {
  description = "Initial database storage (gp3)."
  type        = number
  default     = 20
}

variable "db_max_allocated_storage_gb" {
  description = "Storage autoscaling ceiling. Keeps costs bounded."
  type        = number
  default     = 50
}

variable "db_backup_retention_days" {
  description = "Days of automated backups (point-in-time recovery window)."
  type        = number
  default     = 7
}

variable "db_multi_az" {
  description = "A standby in a second zone: roughly doubles the database cost. Off for a portfolio deployment."
  type        = bool
  default     = false
}

variable "deletion_protection" {
  description = "Stops `terraform destroy` deleting the database and load balancer by accident. Set false deliberately before tearing down."
  type        = bool
  default     = true
}

variable "log_retention_days" {
  description = "How long CloudWatch keeps application logs."
  type        = number
  default     = 30
}

variable "alert_email" {
  description = "Email for CloudWatch alarms (empty: no subscription). AWS sends a confirmation email first."
  type        = string
  default     = ""
}

variable "github_repository" {
  description = "owner/name of the GitHub repository allowed to deploy, e.g. your-user/tradeledger."
  type        = string
}

variable "create_github_oidc_provider" {
  description = "false if the account already has the token.actions.githubusercontent.com OIDC provider."
  type        = bool
  default     = true
}
