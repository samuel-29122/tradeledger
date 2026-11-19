"""Gunicorn settings for the container (Phase 8).

Gunicorn is the production web server: it runs several copies (workers) of
the Flask app and passes HTTP requests to them. Flask's built-in server is
for development only.

Every value can be overridden with an environment variable of the same
purpose, so ECS task definitions do not need a new image to tune them.
"""

import os

bind = "0.0.0.0:8000"

# Workers are processes; each has its own PostgreSQL pool (opened after the
# fork). Total connections = containers x workers x TRADELEDGER_DB_POOL_MAX.
workers = int(os.environ.get("WEB_CONCURRENCY", "2"))
worker_class = "gthread"
threads = int(os.environ.get("GUNICORN_THREADS", "4"))

# A request taking longer than this is killed and logged. The dashboard
# for 10,000 trades took under 1 s in Phase 7/8 measurements.
timeout = int(os.environ.get("GUNICORN_TIMEOUT", "30"))
graceful_timeout = 20
# Longer than the AWS load balancer's 60 s idle timeout, so the balancer,
# not Gunicorn, closes idle connections (avoids sporadic 502 errors).
keepalive = 75

# Restart each worker after a number of requests (with jitter) as a
# guard against slow memory growth.
max_requests = 1000
max_requests_jitter = 100

# TradeLedger writes its own structured access log (observability.py).
accesslog = None
errorlog = "-"
loglevel = os.environ.get("GUNICORN_LOG_LEVEL", "info")

# Proxy headers are handled by the app (TRADELEDGER_PROXY_HOPS), never by
# Gunicorn trusting whatever address happens to connect.
forwarded_allow_ips = "127.0.0.1"

# Each worker creates the app (and its connection pool) itself.
preload_app = False
