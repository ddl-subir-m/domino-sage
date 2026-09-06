"""A viewer who arrives mid-boot reads a sentence, not the proxy's 502.

Domino calls a workspace session running when its execution is up. `app.sh` then resolves the venv,
imports the package and binds the port — seconds later on a cold container — and for that whole gap
the workspace proxy has no upstream and answers `502 Bad Gateway`. That was the first page a new
viewer ever saw.

It was raced twice and lost twice, and it cannot be won from the server side: the workspace ingress
authenticates by browser session cookie and gives a flat 404 to everything else, so a probe run from
another container reads the same 404 on a builder that is up and on one that never existed. So the
gap is filled instead of raced — `boot_page.py` holds the port from the first second, and `run()`
takes it back in the instant before uvicorn binds.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from sage.orchestrator import app as orch_app
from sage.orchestrator import boot_page

REPO = Path(__file__).resolve().parents[2]
APP_SH = REPO / "environment" / "app.sh"
BOOT_PAGE = REPO / "backend" / "sage" / "orchestrator" / "boot_page.py"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _wait_for(url: str, timeout_s: float = 10.0) -> httpx.Response:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            return httpx.get(url, timeout=1.0)
        except httpx.HTTPError:
            time.sleep(0.05)
    raise AssertionError(f"nothing answered {url} in {timeout_s}s")


@pytest.fixture()
def placeholder():
    """The real thing, as a subprocess on the real port — the same shape `app.sh` starts."""
    port = _free_port()
    env = {**os.environ, "SAGE_CONTROL_HOST": "127.0.0.1", "SAGE_CONTROL_PORT": str(port)}
    proc = subprocess.Popen([sys.executable, str(BOOT_PAGE)], env=env)
    try:
        _wait_for(f"http://127.0.0.1:{port}/")
        yield port, proc
    finally:
        proc.kill()
        proc.wait(timeout=5)


# --- what the viewer reads ----------------------------------------------------------------------

def test_a_viewer_who_arrives_mid_boot_gets_a_page_and_not_a_gateway_error(placeholder):
    port, _ = placeholder
    r = httpx.get(f"http://127.0.0.1:{port}/")

    assert r.status_code == 503                       # honest: not yet, come back
    assert "Your workspace is starting" in r.text
    assert "text/html" in r.headers["content-type"]


def test_the_page_brings_itself_back(placeholder):
    """Nobody is told to reload. The whole point is that the wait resolves on its own."""
    port, _ = placeholder
    body = httpx.get(f"http://127.0.0.1:{port}/").text

    assert 'http-equiv="refresh"' in body
    assert httpx.get(f"http://127.0.0.1:{port}/").headers["retry-after"] == "2"


def test_every_path_waits_the_same_way(placeholder):
    """There is no app behind this yet, so routing would be a fiction. A viewer who deep-linked
    waits on the same page as one who opened the root, and the refresh lands them where they asked."""
    port, _ = placeholder
    for path in ("/", "/build", "/preview/", "/api/projects", "/some/proxy/prefix/"):
        r = httpx.get(f"http://127.0.0.1:{port}{path}")
        assert r.status_code == 503, path
        assert "Your workspace is starting" in r.text, path


def test_healthz_says_starting_in_the_words_a_probe_reads(placeholder):
    port, _ = placeholder
    r = httpx.get(f"http://127.0.0.1:{port}/healthz")

    assert r.status_code == 503                        # the one status the door reads as "keep waiting"
    assert r.json() == {"status": "starting"}


def test_a_long_boot_stops_promising_a_few_seconds():
    """A builder that never binds the port would otherwise refresh a reassuring sentence forever.
    Serving on is still right — exiting would hand the viewer back the 502 this file removes."""
    early = boot_page.page(1.0)
    late = boot_page.page(boot_page.PATIENCE_S + 1)

    assert "usually takes a few seconds" in early
    assert "taking longer than expected" in late
    assert "stop the workspace and start it again" in late
    assert 'content="10"' in late                       # and it backs off while it waits


def test_the_page_names_nothing_the_brand_pack_renames():
    """This is the one surface that renders before the pack can be read, so its copy is written
    with no word an OEM overlay would rename. That is the reason it never calls brand.text()."""
    body = boot_page.page(1.0) + boot_page.page(boot_page.PATIENCE_S + 1)

    for word in ("Sage", "Domino", "Workbench", "Project", "Builder"):
        assert word not in body, word


# --- the handoff --------------------------------------------------------------------------------

def test_the_server_takes_the_port_back_before_it_binds(placeholder, monkeypatch):
    port, proc = placeholder
    monkeypatch.setenv("SAGE_BOOT_PAGE_PID", str(proc.pid))

    orch_app._release_boot_page("127.0.0.1", port)

    # Returns only once the port is actually free, so uvicorn cannot lose a race with a process we
    # are the one killing: binding it here has to succeed on the first try.
    with socket.socket() as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", port))
    assert proc.poll() is not None


@pytest.mark.parametrize("pid", ["", "not-a-pid", "999999999"])
def test_no_placeholder_to_release_is_not_a_failed_boot(pid, monkeypatch):
    """Every failure is a no-op on purpose: an image with no python3, a pid already gone, a pid we
    may not signal. Each leaves the boot exactly as it was before this existed."""
    monkeypatch.setenv("SAGE_BOOT_PAGE_PID", pid)

    orch_app._release_boot_page("127.0.0.1", _free_port())   # must not raise


# --- the entrypoint that starts it ----------------------------------------------------------------

def test_app_sh_starts_the_placeholder_before_anything_slow():
    """It has to beat uv resolving the venv and the package import — those are the gap. Started
    with the system python3 by path, because needing the venv would put it on the wrong side of it."""
    sh = APP_SH.read_text()

    assert "boot_page.py" in sh
    assert "SAGE_BOOT_PAGE_PID" in sh
    assert sh.index("boot_page.py") < sh.index("exec uv run")
    assert "uv run" not in sh[sh.index("boot_page.py") - 200:sh.index("boot_page.py")]


def test_the_placeholder_needs_nothing_the_venv_provides():
    """Run by path with whatever python3 the image has, so it must import stdlib only. An import
    added here would silently put the page back on the far side of the gap it exists to cover."""
    import ast

    tree = ast.parse(BOOT_PAGE.read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])

    assert imported <= set(sys.stdlib_module_names), imported - set(sys.stdlib_module_names)
