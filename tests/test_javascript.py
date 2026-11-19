"""Runs the JavaScript unit tests (tests/js/*.test.js) with Node.js.

Node's built-in test runner needs no npm packages. If Node.js is not
installed, this test is skipped and says so; the Python suite is unaffected.
Run them directly with:   node --test tests/js/*.test.js
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

JS_TESTS = Path(__file__).parent / "js"


def find_node():
    return (
        os.environ.get("NODE")
        or shutil.which("node")
        or ("/opt/node22/bin/node" if Path("/opt/node22/bin/node").exists() else None)
    )


def test_javascript_unit_tests_pass():
    node = find_node()
    if node is None:
        pytest.skip("Node.js is not installed")
    files = sorted(str(path) for path in JS_TESTS.glob("*.test.js"))
    assert files, "no JavaScript test files found"
    result = subprocess.run([node, "--test", *files], capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "# fail 0" in result.stdout
