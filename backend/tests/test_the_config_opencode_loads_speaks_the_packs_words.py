"""Every config Sage installs has to be the voiced one, because any of them may be the one read.

Measured on opencode-ai@1.18.4: project config outranks both custom (`OPENCODE_CONFIG`) and global.
But project config is resolved off the git root of the SESSION directory, not the server's cwd, and
every Sage session runs under the workspace volume — so the winning slot is never ours to fill.
What actually carries the pack's words is the GLOBAL copy, which OpenCode demonstrably loads and
which `driver/server.py` also points OPENCODE_CONFIG at.

This file used to claim the opposite, and the claim survived long enough to misdirect two
diagnoses of a leak that was really an unvoiced AGENTS.md on the volume (#202). So the tests below
pin what is installed, not a precedence order: both copies voiced, both dialling the shim, and the
checked-in template left tokenised on disk. See the reopened #199 for the measurement.
"""
import json
from pathlib import Path

import pytest

CONFIG = {
    "model": "sage-gateway/claude",
    "mcp": {
        "sage-live-read": {
            "type": "remote",
            "url": "http://localhost:8080/mcp/live-read",
            "enabled": True,
        }
    },
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
    what those two report — the file the server sits beside, aligned with the global copy it loads.
    """
    from sage.orchestrator.service import _opencode_base_port

    _, project_dir = installed
    options = json.loads((project_dir / "opencode.json").read_text())["provider"]["sage-gateway"]["options"]

    assert options["baseURL"] == "http://127.0.0.1:9999/v1"
    assert _opencode_base_port(project_dir) == 9999


def test_the_installed_config_dials_the_live_read_tools_on_that_same_port(installed):
    """The Live read MCP server is this same process, so its url moves with the port too.

    It did not, and the failure was silent in both directions: on Domino the shim serves :8888
    while the checked-in url says :8080, OpenCode drops an unreachable MCP server without a word,
    and the agent answers "I don't have a live-read tool available" — the very sentence ADR-0041
    was written to delete. Nothing pointed at it, because the baseURL beside it was rewritten and
    looked like the whole job. Both copies are checked: the global one is the one OpenCode loads,
    and the project one is what a session that did start there would read instead.
    """
    import os

    src_dir, project_dir = installed
    for cfg_path in (project_dir / "opencode.json",
                     Path(os.path.expanduser("~/.config/opencode")) / "opencode.json"):
        servers = json.loads(cfg_path.read_text())["mcp"]
        assert servers["sage-live-read"]["url"] == "http://localhost:9999/mcp/live-read", cfg_path

    # And the template on disk keeps its own port, like every other value in it.
    assert json.loads((src_dir / "opencode.json").read_text())["mcp"] == CONFIG["mcp"]


def test_the_project_dir_is_a_git_root_or_it_is_no_project_at_all(installed):
    """Kept as a belt, not as the mechanism. A session started in the server's own cwd — which is
    not how Sage runs one — resolves its project root here, and without a git root it would report
    project "global" at "/" instead. Cheap, and it is the case `/opt/sage` used to cover badly.
    """
    _, project_dir = installed
    assert (project_dir / ".git").exists()


def test_the_global_config_carries_no_unresolved_pack_tokens(installed):
    """The copy OpenCode is actually observed to load, so the one the leak would come out of.

    `driver/server.py` points OPENCODE_CONFIG here too, which makes this file both slots Sage can
    fill. It is asserted on raw text for the same reason as the dir above: a token anywhere in the
    file is a token a user can be shown.
    """
    import os

    text = (Path(os.path.expanduser("~/.config/opencode")) / "opencode.json").read_text()

    assert "{assistantName}" not in text
    assert "{dataSource}" not in text
    assert "Acme" in json.loads(text)["agent"]["sage-chat"]["prompt"]


def test_the_checked_in_source_is_read_and_never_written(installed):
    """The template stays tokenised on disk so a pack never writes its words into a repo file —
    and so `/opt/sage` keeps a clean `git status`, which `app.sh` reads before self-updating.
    """
    src_dir, _ = installed
    assert json.loads((src_dir / "opencode.json").read_text()) == CONFIG


def test_the_opencode_server_is_given_that_dir_as_its_cwd():
    """Not the mechanism — the point is only that the server does not sit in `/opt/sage`, whose
    `opencode.json` is the unvoiced template, and that `_opencode_base_port` reads the dir it
    is actually in."""
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
