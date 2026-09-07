"""OpenCode's PROJECT config wins, so it has to be ours, and it has to be the voiced one (#199).

Measured on opencode-ai@1.18.4 with a marker prompt in each source: project config beat both
custom (`OPENCODE_CONFIG`) and global. `/opt/sage` is a git checkout, so while the OpenCode server
ran there, `/opt/sage/opencode.json` — the *unvoiced* template — was project config and won every
boot. Chat answered "a Snowflake connection is called a **{dataSource}**", and `sage-plan`, sharing
the session, copied the braces into a plan document in front of the person who asked for it.

These tests pin the shape that reverses that precedence: the server gets a cwd of its own, holding
only the voiced copy, and that dir is a git root so the copy counts as project config at all.
"""
import json
from pathlib import Path

import pytest

CONFIG = {
    "model": "sage-gateway/claude",
    "provider": {
        "sage-gateway": {
            "name": "Sage Enforcement Shim",
            "options": {"baseURL": "http://127.0.0.1:8080/v1", "name": "google"},
        }
    },
    "agent": {
        "sage-chat": {"prompt": "You are {assistantName}. A connection is a {dataSource}."},
        "sage-build": {"prompt": "You build apps."},
    },
}


@pytest.fixture()
def installed(tmp_path, monkeypatch):
    """Run `_install_opencode_config` against a throwaway HOME and return where things landed."""
    from sage.orchestrator.app import _install_opencode_config, _opencode_project_dir

    src_dir = tmp_path / "repo"
    src_dir.mkdir()
    (src_dir / "opencode.json").write_text(json.dumps(CONFIG))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "brand.json").write_text(json.dumps({"productName": "Acme"}))
    monkeypatch.setenv("SAGE_BRAND_FILE", str(tmp_path / "brand.json"))

    _install_opencode_config(src_dir, 9999)
    return src_dir, _opencode_project_dir()


def test_the_project_config_carries_no_unresolved_pack_tokens(installed):
    """The one file that outranks the rest must already say the pack's words.

    Asserted on the raw text, not on one prompt: a token anywhere in the file is a token the user
    can be shown, and the bug shipped through a prompt nobody was looking at.
    """
    _, project_dir = installed
    text = (project_dir / "opencode.json").read_text()

    assert "{assistantName}" not in text
    assert "{dataSource}" not in text
    assert "Acme" in json.loads(text)["agent"]["sage-chat"]["prompt"]


def test_the_project_config_dials_the_port_the_shim_serves(installed):
    """Voicing the config is worthless if it routes inference away from the shim.

    `_opencode_base_port` reads this same dir for /api/diag and the turn summary, so this is also
    what those two report — and after the cwd moved they report the file OpenCode really loads.
    """
    from sage.orchestrator.service import _opencode_base_port

    _, project_dir = installed
    options = json.loads((project_dir / "opencode.json").read_text())["provider"]["sage-gateway"]["options"]

    assert options["baseURL"] == "http://127.0.0.1:9999/v1"
    assert _opencode_base_port(project_dir) == 9999


def test_the_project_dir_is_a_git_root_or_it_is_no_project_at_all(installed):
    """Discovery walks up from the server's cwd for a git root. Without one, OpenCode reports
    project "global" at "/" and finds no project config — the voiced copy would sit there unread.
    """
    _, project_dir = installed
    assert (project_dir / ".git").exists()


def test_the_checked_in_source_is_read_and_never_written(installed):
    """The template stays tokenised on disk so a pack never writes its words into a repo file —
    and so `/opt/sage` keeps a clean `git status`, which `app.sh` reads before self-updating.
    """
    src_dir, _ = installed
    assert json.loads((src_dir / "opencode.json").read_text()) == CONFIG


def test_the_opencode_server_is_given_that_dir_as_its_cwd():
    """The install is inert unless the server actually runs there — that is the whole mechanism."""
    from sage.orchestrator import app

    assert app.orchestrator._opencode_cwd == app._opencode_project_dir()


def test_the_server_creates_its_cwd_rather_than_failing_to_spawn(monkeypatch, tmp_path):
    """The cwd is now a dir Sage owns instead of the repo, so it can be absent — a local run that
    skips the boot path never installs it. Popen does not create a missing cwd; it raises.
    """
    from sage.driver import server as drv

    seen = {}

    class _Proc:
        stdout = None

        def __init__(self, *a, **kw):
            seen["cwd"] = kw["cwd"]

    monkeypatch.setattr(drv.subprocess, "Popen", _Proc)
    monkeypatch.setattr(drv.threading, "Thread", lambda **kw: type("T", (), {"start": lambda s: None})())
    monkeypatch.setattr(drv, "_VOICED_CONFIG", tmp_path / "nowhere" / "opencode.json")

    cwd = tmp_path / "not-yet-there"
    server = drv.OpenCodeServer(cwd=cwd)
    server._url = "http://127.0.0.1:1"
    server._ready.set()
    server.start()

    assert Path(seen["cwd"]) == cwd
    assert cwd.is_dir()
