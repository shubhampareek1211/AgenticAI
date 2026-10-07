"""Run browser-script regressions without a provider or a browser installation."""

import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(
    shutil.which("node") is None, reason="Node is required for browser-script tests."
)
def test_browser_chat_regressions():
    result = subprocess.run(
        ["node", "--test", str(Path(__file__).with_name("browser_chat.cjs"))],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
