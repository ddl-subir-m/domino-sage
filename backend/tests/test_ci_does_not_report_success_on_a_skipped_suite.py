"""CI cannot report success on a suite it never really ran (#166).

Most test files in this directory carry `skipif(shutil.which("node") is None)`, because the `.mjs`
harnesses under `tests/js/` are the only test of the workbench JavaScript — there is no vitest and no
jest. A workflow that installs Python but not Node therefore passes green while testing none of the
front end. That is worse than no CI, because it looks like coverage.

These tests make that absence loud. They assert nothing about the product; they assert that the
runner is the one the suite needs. Off CI they are inert, because a missing tool on a laptop is the
developer's own informed choice. They bind only when `CI` is set, which GitHub Actions does.

Deliberately narrow: this is not a ban on skipping. It names the two tools whose absence silently
removes real coverage, and leaves every other skip alone.
"""
from __future__ import annotations

import os
import shutil

import pytest

pytestmark = pytest.mark.skipif(os.getenv("CI") is None, reason="binds on CI only (#166)")


def test_node_is_on_path_so_the_js_harnesses_actually_run():
    assert shutil.which("node") is not None, (
        "node is not on PATH, so every .mjs harness under tests/js/ skipped itself and the "
        "workbench JavaScript went untested. Install Node in the workflow."
    )


def test_ripgrep_is_on_path_so_the_greppability_test_actually_runs():
    assert shutil.which("rg") is not None, (
        "ripgrep is not on PATH, so the test that the archive stays greppable once git ignores "
        "it skipped itself. Install ripgrep in the workflow."
    )
