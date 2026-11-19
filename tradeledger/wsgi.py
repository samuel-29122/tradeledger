"""WSGI entry point for production servers (Phase 8).

    gunicorn tradeledger.wsgi:app

The configuration comes from TRADELEDGER_CONFIG (the Docker image sets
"production"). Importing this module creates the app, so it must only be
imported by the server, never by tests or the CLI.
"""

from . import create_app

app = create_app()
