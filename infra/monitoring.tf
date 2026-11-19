# CloudWatch alarms -> SNS topic -> email (optional).
# Each standard alarm costs about USD 0.10 per month.

resource "aws_sns_topic" "alerts" {
  name = "${local.name}-alerts"
}

resource "aws_sns_topic_subscription" "email" {
  count     = var.alert_email == "" ? 0 : 1
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

resource "aws_cloudwatch_metric_alarm" "app_5xx" {
  alarm_name          = "${local.name}-5xx"
  alarm_description   = "The app returned 5 or more server errors in 5 minutes."
  namespace           = "AWS/ApplicationELB"
  metric_name         = "HTTPCode_Target_5XX_Count"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 5
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  dimensions          = { LoadBalancer = aws_lb.this.arn_suffix }
  alarm_actions       = [aws_sns_topic.alerts.arn]
}

resource "aws_cloudwatch_metric_alarm" "unhealthy_tasks" {
  alarm_name          = "${local.name}-unhealthy"
  alarm_description   = "At least one task failed its readiness check for 3 minutes."
  namespace           = "AWS/ApplicationELB"
  metric_name         = "UnHealthyHostCount"
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 3
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  dimensions = {
    LoadBalancer = aws_lb.this.arn_suffix
    TargetGroup  = aws_lb_target_group.app.arn_suffix
  }
  alarm_actions = [aws_sns_topic.alerts.arn]
}

resource "aws_cloudwatch_metric_alarm" "db_cpu" {
  alarm_name          = "${local.name}-db-cpu"
  alarm_description   = "Database CPU above 80% for 15 minutes."
  namespace           = "AWS/RDS"
  metric_name         = "CPUUtilization"
  statistic           = "Average"
  period              = 300
  evaluation_periods  = 3
  threshold           = 80
  comparison_operator = "GreaterThanThreshold"
  dimensions          = { DBInstanceIdentifier = aws_db_instance.this.identifier }
  alarm_actions       = [aws_sns_topic.alerts.arn]
}

resource "aws_cloudwatch_metric_alarm" "db_storage" {
  alarm_name          = "${local.name}-db-storage"
  alarm_description   = "Less than 2 GB of database storage left."
  namespace           = "AWS/RDS"
  metric_name         = "FreeStorageSpace"
  statistic           = "Minimum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 2147483648
  comparison_operator = "LessThanThreshold"
  dimensions          = { DBInstanceIdentifier = aws_db_instance.this.identifier }
  alarm_actions       = [aws_sns_topic.alerts.arn]
}

# Saved CloudWatch Logs Insights queries for troubleshooting.
resource "aws_cloudwatch_query_definition" "errors" {
  name            = "${local.name}/server-errors"
  log_group_names = [aws_cloudwatch_log_group.app.name]
  query_string    = <<-EOQ
    fields @timestamp, request_id, path, status, message
    | filter level = "ERROR" or status >= 500
    | sort @timestamp desc
    | limit 50
  EOQ
}

resource "aws_cloudwatch_query_definition" "failed_sign_ins" {
  name            = "${local.name}/failed-sign-ins"
  log_group_names = [aws_cloudwatch_log_group.app.name]
  query_string    = <<-EOQ
    fields @timestamp, event, reason, email_ref
    | filter event in ["auth.login.failed", "auth.login.throttled", "auth.register.throttled"]
    | stats count(*) by bin(15m), event
  EOQ
}
