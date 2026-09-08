"""The profile menu on the top right: Account settings, Log out, and nothing of Organization.

Log out used to toast "open the platform". That is not logging out. `/logout` on the main Domino
host is the door that ends the session; from the published App that host is `apps.` away, so the
same resolver Manage already uses is what lands there.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "js" / "account_menu_harness.mjs"


def _menu(**spec) -> dict:
    if shutil.which("node") is None:
        pytest.skip("node is not on PATH (it is in the Sage image)")
    out = subprocess.run(
        ["node", str(_HARNESS)],
        input=json.dumps(spec),
        check=False, capture_output=True, text=True, timeout=60,
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_the_account_menu_offers_settings_and_log_out():
    """Organization is a platform screen Sage does not own. A stub that said so was worse than
    leaving it off. Account settings is ours; Log out is the platform's session."""
    drawn = _menu()
    assert drawn["labels"] == ["Account settings", "Log out"]
    assert drawn["keys"] == ["account", "logout"]
    assert "Organization" not in drawn["labels"]
    assert "Sign out" not in drawn["labels"]


def test_log_out_from_the_published_app_lands_on_the_main_host():
    """The App is served from apps.<host>; the session cookie is not. `/logout` on the apps origin
    404s, so the same strip Manage already uses is what lands on the door that ends the session."""
    drawn = _menu(
        click="logout",
        hostname="apps.cloud-dogfood.domino.tech",
        protocol="https:",
    )
    assert drawn["gone"] == ["https://cloud-dogfood.domino.tech/logout"]
    assert drawn["said"] == []
    assert drawn["settingsOpen"] is False


def test_log_out_from_a_builder_is_the_same_path():
    """A Builder is already on the main host, so the path is enough."""
    drawn = _menu(
        click="logout",
        hostname="cloud-dogfood.domino.tech",
        protocol="https:",
        href="https://cloud-dogfood.domino.tech/u/me/sage/notebook",
    )
    assert drawn["gone"] == ["/logout"]
    assert drawn["said"] == []


def test_account_settings_still_opens_the_drawer():
    drawn = _menu(click="account")
    assert drawn["settingsOpen"] is True
    assert drawn["gone"] == []
    assert drawn["said"] == []
