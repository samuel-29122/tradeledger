"""Phase 8: static checks of the deployment files.

These read the Dockerfile, compose.yaml, the GitHub workflows and the
Terraform files as TEXT and check security properties. They do not build an
image, start containers or contact AWS: that is the job of the CI "docker"
and "terraform" jobs and of a real deployment. Passing here proves the
files say the right things, not that they run.
"""

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
INFRA = ROOT / "infra"


def read(path):
    return (ROOT / path).read_text(encoding="utf-8")


# ---------- Dockerfile ----------


def dockerfile_instructions():
    text = re.sub(r"\\\n", " ", read("Dockerfile"))
    return [line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith("#")]


def test_image_runs_as_a_fixed_non_root_user():
    users = [line for line in dockerfile_instructions() if line.startswith("USER ")]
    assert users and users[-1] == "USER 10001:10001"


def test_image_uses_gunicorn_not_the_flask_dev_server():
    lines = dockerfile_instructions()
    cmd = next(line for line in lines if line.startswith("CMD "))
    assert "gunicorn" in cmd and "tradeledger.wsgi:app" in cmd
    assert not any("flask run" in line or "FLASK_DEBUG" in line for line in lines)


def test_image_bakes_in_no_secrets():
    for line in dockerfile_instructions():
        if line.startswith(("ENV ", "ARG ")):
            assert not re.search(r"SECRET|PASSWORD|TOKEN|AWS_", line), line
    assert "TRADELEDGER_CONFIG=production" in read("Dockerfile")


def test_image_has_a_liveness_healthcheck_and_exec_entrypoint():
    assert "HEALTHCHECK" in read("Dockerfile") and "/health/live" in read("Dockerfile")
    entrypoint = read("docker/entrypoint.sh")
    assert 'exec "$@"' in entrypoint  # Gunicorn becomes PID 1 and receives SIGTERM


def test_dockerignore_is_an_allow_list():
    rules = [r.strip() for r in read(".dockerignore").splitlines() if r.strip() and not r.startswith("#")]
    assert rules[0] == "*"
    allowed = {r[1:] for r in rules if r.startswith("!")}
    assert allowed == {
        "tradeledger/",
        "requirements.txt",
        "constraints.txt",
        "gunicorn.conf.py",
        "docker/entrypoint.sh",
    }


def test_production_wsgi_module_uses_the_environment_config():
    assert "app = create_app()" in read("tradeledger/wsgi.py")


# ---------- Docker Compose ----------


@pytest.fixture(scope="module")
def compose():
    return yaml.safe_load(read("compose.yaml"))


def test_database_is_not_published(compose):
    db = compose["services"]["db"]
    assert "ports" not in db and "expose" not in db
    assert compose["networks"]["backend"]["internal"] is True


def test_app_is_published_on_localhost_only(compose):
    assert compose["services"]["app"]["ports"] == ["127.0.0.1:8000:8000"]


def test_secrets_are_files_not_environment_values(compose):
    app_env = compose["services"]["app"]["environment"]
    assert "SECRET_KEY" not in app_env and "DB_PASSWORD" not in app_env
    assert app_env["SECRET_KEY_FILE"].startswith("/run/secrets/")
    assert app_env["DB_PASSWORD_FILE"].startswith("/run/secrets/")
    assert compose["services"]["db"]["environment"]["POSTGRES_PASSWORD_FILE"].startswith("/run/secrets/")
    for secret in compose["secrets"].values():
        assert secret["file"].startswith("./secrets/")


def test_app_container_is_hardened(compose):
    app = compose["services"]["app"]
    assert app["read_only"] is True
    assert app["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in app["security_opt"]
    assert app["depends_on"]["db"]["condition"] == "service_healthy"
    assert app["environment"]["TRADELEDGER_CONFIG"] == "production"
    assert app["environment"]["TRADELEDGER_REGISTRATION_ENABLED"] == "0"


def test_secret_files_and_state_are_ignored_by_git():
    ignored = read(".gitignore")
    for pattern in ("secrets/*", "*.tfstate", ".terraform/", "*.tfvars", ".env", "*.db"):
        assert pattern in ignored


# ---------- Workflows ----------


@pytest.fixture(scope="module")
def deploy():
    return yaml.safe_load(read(".github/workflows/deploy.yml"))


@pytest.fixture(scope="module")
def ci():
    return yaml.safe_load(read(".github/workflows/ci.yml"))


def test_deploy_is_manual_only(deploy):
    triggers = deploy[True]  # YAML 1.1 reads the key "on" as True
    assert set(triggers) == {"workflow_dispatch"}


def test_deploy_needs_approval_and_uses_oidc(deploy):
    job = deploy["jobs"]["deploy"]
    assert job["environment"] == "production"
    assert job["permissions"] == {"contents": "read", "id-token": "write"}
    assert job["if"] == "vars.DEPLOY_ENABLED == 'true'"
    text = read(".github/workflows/deploy.yml")
    assert "role-to-assume" in text
    for forbidden in ("aws-access-key-id", "aws-secret-access-key", "AWS_SECRET_ACCESS_KEY", "secrets.AWS"):
        assert forbidden not in text


def test_deploy_snapshots_before_migrating(deploy):
    names = [step["name"] for step in deploy["jobs"]["deploy"]["steps"]]
    assert names.index("Snapshot the database") < names.index("Run database migrations (one-off task)")
    assert names.index("Run database migrations (one-off task)") < names.index(
        "Deploy the new task definition"
    )


def test_ci_has_no_deployment_or_cloud_credentials(ci):
    text = read(".github/workflows/ci.yml")
    assert ci["permissions"] == {"contents": "read"}
    assert "id-token" not in text and "configure-aws-credentials" not in text


def test_ci_runs_postgres_docker_and_terraform_checks(ci):
    jobs = ci["jobs"]
    assert jobs["postgres"]["services"]["postgres"]["image"] == "postgres:16"
    assert "TRADELEDGER_TEST_DATABASE_URL" in jobs["postgres"]["env"]
    assert "TRADELEDGER_PG_DRIVER" not in read(".github/workflows/ci.yml")  # CI uses the real driver
    docker_steps = " ".join(step.get("run", "") for step in jobs["docker"]["steps"])
    assert "docker build" in docker_steps and "compose_smoke_test.sh" in docker_steps
    terraform_steps = " ".join(step.get("run", "") for step in jobs["terraform"]["steps"])
    assert "terraform validate" in terraform_steps and "-backend=false" in terraform_steps


# ---------- Terraform ----------


def terraform_text():
    return "\n".join(path.read_text(encoding="utf-8") for path in sorted(INFRA.glob("*.tf")))


def block(text, kind, name):
    """The body of `resource "kind" "name" { ... }` (brace-matched)."""
    match = re.search(rf'resource "{kind}" "{name}" \{{', text)
    assert match, (kind, name)
    depth, i = 1, match.end()
    while depth:
        depth += {"{": 1, "}": -1}.get(text[i], 0)
        i += 1
    return text[match.end() : i - 1]


def test_terraform_references_resolve():
    """A cheap substitute for `terraform validate` (which needs the
    Terraform binary): every var., local. and resource reference points at
    something that is declared."""
    text = terraform_text()
    code = "\n".join(line.split("#")[0] for line in text.splitlines())
    variables = set(re.findall(r'^variable "(\w+)"', text, re.M))
    resources = set(re.findall(r'^resource "(\w+)" "(\w+)"', text, re.M))
    data_sources = set(re.findall(r'^data "(\w+)" "(\w+)"', text, re.M))
    local_names = set()
    for body in re.findall(r"^locals \{(.*?)^\}", text, re.M | re.S):
        local_names |= set(re.findall(r"^  (\w+)\s*=", body, re.M))
    assert set(re.findall(r"\bvar\.(\w+)", code)) <= variables
    assert set(re.findall(r"\blocal\.(\w+)", code)) <= local_names
    for kind, name in re.findall(r"\b(aws_\w+)\.(\w+)\b", code):
        if kind in {r[0] for r in resources}:
            assert (kind, name) in resources, f"{kind}.{name}"
    for kind, name in re.findall(r"\bdata\.(aws_\w+)\.(\w+)", code):
        assert (kind, name) in data_sources, f"data.{kind}.{name}"
    assert code.count("{") == code.count("}")


def test_database_is_private_encrypted_and_backed_up():
    rds = block(terraform_text(), "aws_db_instance", "this")
    assert re.search(r"publicly_accessible\s*=\s*false", rds)
    assert re.search(r"storage_encrypted\s*=\s*true", rds)
    assert re.search(r"manage_master_user_password\s*=\s*true", rds)
    assert re.search(r"skip_final_snapshot\s*=\s*false", rds)
    assert "backup_retention_period" in rds and "deletion_protection" in rds
    assert 'name         = "rds.force_ssl"' in terraform_text()


def test_only_the_load_balancer_is_open_to_the_internet():
    text = terraform_text()
    for name in re.findall(r'resource "aws_vpc_security_group_ingress_rule" "(\w+)"', text):
        rule = block(text, "aws_vpc_security_group_ingress_rule", name)
        if "0.0.0.0/0" in rule:
            assert "aws_security_group.alb.id" in rule, name
    db_rule = block(text, "aws_vpc_security_group_ingress_rule", "db_from_app")
    assert "referenced_security_group_id = aws_security_group.app.id" in db_rule


def test_no_credentials_or_secret_values_in_terraform():
    text = terraform_text() + (INFRA / "terraform.tfvars.example").read_text()
    assert not re.search(r"AKIA[0-9A-Z]{16}", text)
    for forbidden in ("access_key", "secret_key =", "secret_string", "random_password"):
        assert forbidden not in text, forbidden
    # A literal password assignment (manage_master_user_password = true is fine).
    assert not re.search(r'\bpassword\s*=\s*"', text)


def test_iam_never_grants_wildcard_actions():
    text = terraform_text()
    for actions in re.findall(r"actions\s*=\s*\[([^\]]*)\]", text):
        for action in re.findall(r'"([^"]+)"', actions):
            assert action != "*" and not action.endswith(":*"), action


def test_task_role_has_no_permissions():
    text = terraform_text()
    assert "aws_iam_role.task.name" not in text and "aws_iam_role.task.id" not in text


def test_github_role_is_limited_to_this_repository_environment():
    text = terraform_text()
    assert '"repo:${var.github_repository}:environment:production"' in text
    assert "token.actions.githubusercontent.com:aud" in text


def test_https_only_and_host_restricted():
    text = terraform_text()
    http = block(text, "aws_lb_listener", "http")
    assert 'type = "redirect"' in http and '"HTTPS"' in http
    https = block(text, "aws_lb_listener", "https")
    assert "TLS13" in https and 'type = "fixed-response"' in https
    assert "host_header" in block(text, "aws_lb_listener_rule", "app")


def test_backend_encrypts_and_locks_state():
    backend = (INFRA / "backend.tf").read_text()
    assert "encrypt      = true" in backend and "use_lockfile = true" in backend


# ---------- The repository holds no secrets ----------


def test_no_secrets_committed_anywhere():
    suspicious = re.compile(
        r"AKIA[0-9A-Z]{16}|-----BEGIN (RSA |EC )?PRIVATE KEY-----|\btl_[A-Za-z0-9_-]{43}\b|ghp_[A-Za-z0-9]{36}"
    )
    skip_dirs = {".git", "node_modules", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache", "instance"}
    for path in ROOT.rglob("*"):
        if path.is_dir() or skip_dirs & set(path.parts) or path.suffix in {".png", ".db", ".pyc"}:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        assert not suspicious.search(text), path
