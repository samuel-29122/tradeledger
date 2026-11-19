# Network layout (2 Availability Zones)
#
#   public subnets   the load balancer, and the app tasks (with a public IP so
#                    they can reach ECR, Secrets Manager and CloudWatch
#                    without a NAT gateway; their security group only accepts
#                    traffic from the load balancer)
#   private subnets  the database only. No route to the internet at all.
#
# Why no NAT gateway: it costs about USD 33 per month per gateway before any
# traffic, more than the app itself. The trade-off is that tasks have public
# IPs (still unreachable from the internet, see security_groups.tf). A
# stricter design puts tasks in private subnets with a NAT gateway or VPC
# endpoints (about USD 7-8 per endpoint per AZ per month).

data "aws_availability_zones" "available" {
  state = "available"
}

locals {
  name = "tradeledger-${var.environment}"
  azs  = slice(data.aws_availability_zones.available.names, 0, 2)
}

resource "aws_vpc" "this" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = { Name = local.name }
}

# Lock down the VPC's default security group: nothing may use it by accident.
resource "aws_default_security_group" "this" {
  vpc_id = aws_vpc.this.id
}

resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id
  tags   = { Name = local.name }
}

resource "aws_subnet" "public" {
  count                   = length(local.azs)
  vpc_id                  = aws_vpc.this.id
  availability_zone       = local.azs[count.index]
  cidr_block              = cidrsubnet(var.vpc_cidr, 8, count.index)
  map_public_ip_on_launch = false # tasks request a public IP explicitly

  tags = { Name = "${local.name}-public-${local.azs[count.index]}" }
}

resource "aws_subnet" "private" {
  count             = length(local.azs)
  vpc_id            = aws_vpc.this.id
  availability_zone = local.azs[count.index]
  cidr_block        = cidrsubnet(var.vpc_cidr, 8, count.index + 10)

  tags = { Name = "${local.name}-private-${local.azs[count.index]}" }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.this.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.this.id
  }

  tags = { Name = "${local.name}-public" }
}

resource "aws_route_table_association" "public" {
  count          = length(aws_subnet.public)
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

# The private route table has only the implicit local route: the database
# subnets cannot reach or be reached from the internet.
resource "aws_route_table" "private" {
  vpc_id = aws_vpc.this.id
  tags   = { Name = "${local.name}-private" }
}

resource "aws_route_table_association" "private" {
  count          = length(aws_subnet.private)
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private.id
}
