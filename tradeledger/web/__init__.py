"""The TradeLedger website: server-rendered pages and HTML forms.

Blueprint "main" (the same name Phase 1 used, so url_for("main.index") and
url_for("main.health") keep working). Modules:

  auth.py         sign in, sign out, registration, session checks (Phase 8)
  account.py      account settings, password change, API tokens (Phase 8),
                  signed-in browsers (Phase 8.5)
  reset.py        one-time password-reset links (Phase 8.5)
  two_step.py     two-step verification: set-up and sign-in (Phase 8.5)
  csrf.py         CSRF tokens for every state-changing form
  filters.py      Jinja filters that format exact Decimals for display
  forms.py        turning request.form into input for validation.py
  views.py        overview, about, health checks
  analytics.py    the /analytics performance dashboard (Phase 6)
  trades.py       journal, add, details, edit, delete
  instruments.py  instrument list and add form

Every write goes through the same repository and validation functions as
the JSON API, so there is one set of business rules, not two. Every read
and write passes the signed-in user's id (auth.current_user_id()).
"""

from flask import Blueprint

main_bp = Blueprint("main", __name__)

# Importing these modules registers their routes and template filters.
from . import (  # noqa: E402,F401
    account,
    analytics,
    auth,
    csrf,
    filters,
    instruments,
    reset,
    trades,
    two_step,
    views,
)

# Request hooks are registered HERE, explicitly, because their order
# matters and must not depend on import order (Phase 7: sorting the import
# line above once swapped them silently; tests caught it).
#   1. load_logged_in_user: read and check the session (g.user).
#   2. require_login: every endpoint not in auth.PUBLIC_ENDPOINTS redirects
#      anonymous visitors to the sign-in page. Running it BEFORE the CSRF
#      check means an anonymous POST is redirected, not told "form expired".
#   3. csrf: check the token on every state-changing request, including the
#      sign-in form itself (which stops "login CSRF").
main_bp.before_request(auth.load_logged_in_user)
main_bp.before_request(auth.require_login)
main_bp.before_request(csrf.check_csrf_token)
