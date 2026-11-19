"""Phase 8.5: the self-hosted Chart.js file and the default configuration.

Plain English: TradeLedger now ships its own copy of Chart.js. These tests
prove the copy is the one that was checked (its fingerprint matches the
configuration), that its licences travel with it, and that development
and production really use it by default, with no outside website.
They run without a browser; tests/test_chartjs_real.py draws the charts.
"""

import base64
import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path

from tests.conftest import ensure_user, sign_in
from tradeledger import config as config_module
from tradeledger import create_app
from tradeledger.db import get_db, init_schema

ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / "tradeledger/static/vendor/chartjs"
LIBRARY = VENDOR / "chart.umd.min.js"


def sri(path):
    return "sha384-" + base64.b64encode(hashlib.sha384(path.read_bytes()).digest()).decode()


def test_bundled_file_matches_the_configured_integrity_hash():
    assert sri(LIBRARY) == config_module.Config.CHART_JS_BUNDLED_INTEGRITY


def test_bundled_file_is_chart_js_4_5_1_with_both_licences():
    header = LIBRARY.read_text(encoding="utf-8")[:600]
    assert "Chart.js v4.5.1" in header and "Released under the MIT License" in header
    assert "@kurkle/color v0.3.2" in header
    assert "MIT License" in (VENDOR / "LICENSE.md").read_text(encoding="utf-8")
    assert "Jukka Kurkela" in (VENDOR / "LICENSE-kurkle-color.md").read_text(encoding="utf-8")


def test_build_notes_record_the_source_commits_and_the_hash():
    notes = (VENDOR / "BUILD.md").read_text(encoding="utf-8")
    assert "9c5cf9fac7ec04a71b516e2aff3f7d76876be369" in notes
    assert "eb0d1db26a35704553c93782bad8c3ac2d971597" in notes
    assert config_module.Config.CHART_JS_BUNDLED_INTEGRITY in notes
    assert "not byte-for-byte the npm file" in notes  # the honest caveat stays


def test_bundled_file_contains_no_source_map_or_remote_reference():
    text = LIBRARY.read_text(encoding="utf-8")
    assert "sourceMappingURL" not in text
    assert not re.search(r"""["'`]https?://(?!www\.chartjs\.org|github\.com/kurkle)""", text[:600])


def config_values(env):
    """Read the Chart.js settings in a fresh interpreter (config.py reads the
    environment once, at import), so the test does not disturb this one."""
    script = (
        "from tradeledger import config as c\n"
        "for n in ('DevelopmentConfig', 'ProductionConfig'):\n"
        "    k = getattr(c, n)\n"
        "    print(n, k.CHART_JS_URL, k.CHART_JS_INTEGRITY)\n"
    )
    clean = {k: v for k, v in os.environ.items() if not k.startswith("TRADELEDGER_CHART_JS")}
    result = subprocess.run(
        [sys.executable, "-c", script],
        env={**clean, **env},
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return {
        name: (url, integrity)
        for name, url, integrity in (line.split() for line in result.stdout.splitlines())
    }


def test_development_and_production_self_host_by_default():
    for url, integrity in config_values({}).values():
        assert url == "/static/vendor/chartjs/chart.umd.min.js"
        assert integrity == sri(LIBRARY)


def test_a_cdn_url_from_the_environment_does_not_inherit_the_bundled_hash():
    cdn = "https://cdn.jsdelivr.net/npm/chart.js@4.5.1/dist/chart.umd.min.js"
    for url, integrity in config_values({"TRADELEDGER_CHART_JS_URL": cdn}).values():
        assert (url, integrity) == (cdn, "None")


def test_dashboard_with_the_default_setting_uses_only_this_site(db_path):
    defaults = config_module.Config
    app = create_app(
        "testing",
        overrides={
            "DATABASE": db_path,
            "CHART_JS_URL": defaults.CHART_JS_BUNDLED_URL,
            "CHART_JS_INTEGRITY": defaults.CHART_JS_BUNDLED_INTEGRITY,
        },
    )
    with app.app_context():
        from tradeledger.seed import seed_sample_data

        init_schema(get_db())
        seed_sample_data(get_db(), user_id=ensure_user(get_db()))
    client = app.test_client()
    sign_in(client)
    response = client.get("/analytics")
    html = response.get_data(as_text=True)
    csp = response.headers["Content-Security-Policy"]
    assert "script-src 'self';" in csp and "jsdelivr" not in csp
    assert f'src="{defaults.CHART_JS_BUNDLED_URL}"' in html
    assert f'integrity="{defaults.CHART_JS_BUNDLED_INTEGRITY}"' in html
    library = client.get(defaults.CHART_JS_BUNDLED_URL)
    assert library.status_code == 200 and library.data == LIBRARY.read_bytes()
    library.close()
    app.extensions["tradeledger_db"].close()


def test_docker_build_context_includes_the_vendor_files():
    ignore = (Path(__file__).resolve().parent.parent / ".dockerignore").read_text(encoding="utf-8")
    assert "!tradeledger/" in ignore
    excluded = [line for line in ignore.splitlines() if "vendor/chartjs" in line and not line.startswith("#")]
    assert excluded == ["tradeledger/static/vendor/chartjs/*.tgz"]
