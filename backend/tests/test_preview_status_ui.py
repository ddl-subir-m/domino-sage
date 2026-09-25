"""Run real Workbench functions with deferred HTTP responses and inspect the rendered controls."""
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(shutil.which("node") is None, reason="node is required for Workbench behavior tests")
def test_preview_status_and_retry_are_app_scoped():
    result = subprocess.run(
        [shutil.which("node"), str(Path(__file__).parent / "js/preview_status_harness.mjs")],
        capture_output=True, text=True, timeout=20, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
