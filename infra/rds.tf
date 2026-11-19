# Amazon RDS for PostgreSQL 16
#
# * Private subnets only, publicly_accessible = false, reachable only from
#   the app's security group.
# * Encrypted at rest (AWS-managed KMS key); rds.force_ssl = 1 refuses any
#   unencrypted connection (the app connects with DB_SSLMODE=require).
# * manage_master_user_password: RDS generates the password and keeps it in
#   Secrets Manager, rotating it automatically. Terraform never sees it, so
#   it is not in the state file.
# * Automated daily backups with point-in-time recovery for
#   db_backup_retention_days; a final snapshot is taken if the database is
#   ever deleted. Deletion protection is on by default.

resource "aws_db_subnet_group" "this" {
  name       = local.name
  subnet_ids = aws_subnet.private[*].id
}

resource "aws_db_parameter_group" "this" {
  name   = "${local.name}-pg16"
  family = "postgres16"

  parameter {
    name         = "rds.force_ssl"
    value        = "1"
    apply_method = "pending-reboot"
  }

  parameter {
    name  = "log_min_duration_statement"
    value = "1000" # log statements slower than 1 s (no parameter values at this level)
  }
}

resource "aws_db_instance" "this" {
  identifier     = local.name
  engine         = "postgres"
  engine_version = "16"
  instance_class = var.db_instance_class

  db_name                     = "tradeledger"
  username                    = "tradeledger_admin"
  manage_master_user_password = true

  allocated_storage     = var.db_allocated_storage_gb
  max_allocated_storage = var.db_max_allocated_storage_gb
  storage_type          = "gp3"
  storage_encrypted     = true

  db_subnet_group_name   = aws_db_subnet_group.this.name
  vpc_security_group_ids = [aws_security_group.db.id]
  publicly_accessible    = false
  multi_az               = var.db_multi_az
  parameter_group_name   = aws_db_parameter_group.this.name

  backup_retention_period   = var.db_backup_retention_days
  backup_window             = "02:00-03:00" # UTC
  maintenance_window        = "sun:03:30-sun:04:30"
  copy_tags_to_snapshot     = true
  deletion_protection       = var.deletion_protection
  skip_final_snapshot       = false
  final_snapshot_identifier = "${local.name}-final"

  auto_minor_version_upgrade      = true
  enabled_cloudwatch_logs_exports = ["postgresql"]
  performance_insights_enabled    = false # extra cost; enable when investigating
  apply_immediately               = false
}
