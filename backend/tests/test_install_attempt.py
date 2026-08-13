"""_install_attempt: which packages a bash command is trying to add.

Feeds the "dependency wanted" log — the evidence for what belongs in the baked template — and
flags a turn that is about to lose its node_modules to npm. Real commands here are copied from
live builds (2026-08-13), not invented.
"""
from __future__ import annotations

from sage.orchestrator.service import _install_attempt


def test_the_command_that_broke_a_live_workspace():
    assert _install_attempt("npm install @acme/react-date-range") == ["@acme/react-date-range"]


def test_a_piped_command_does_not_read_the_pipeline_as_packages():
    # `tail` is a perfectly valid package name, so splitting on shell operators has to come first.
    assert _install_attempt("cd /mnt/code && npm install date-fns 2>&1 | tail -20") == ["date-fns"]


def test_flags_are_skipped_and_versions_kept():
    assert _install_attempt("npm install --save-dev typescript@~6.0.2") == ["typescript@~6.0.2"]


def test_the_other_package_managers():
    assert _install_attempt("yarn add recharts") == ["recharts"]
    assert _install_attempt("pnpm add react-router-dom") == ["react-router-dom"]
    assert _install_attempt("npm i lucide-react") == ["lucide-react"]


def test_several_packages_at_once():
    assert _install_attempt("npm install antd @ant-design/icons") == ["antd", "@ant-design/icons"]


def test_a_bare_reinstall_wants_nothing_new():
    # Destructive all the same, but it is not a signal about what the template is missing.
    assert _install_attempt("npm install") == []
    assert _install_attempt("npm install 2>&1 | tail -20") == []


def test_ordinary_commands_are_not_install_attempts():
    for cmd in ("npm run build 2>&1 | tail -40", "npx tsc -b", "ls node_modules/.bin/",
                "cat package.json | grep -i date-fns || echo \"date-fns not found\""):
        assert _install_attempt(cmd) == [], cmd
